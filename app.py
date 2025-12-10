from flask import Flask, send_file, render_template, redirect, url_for, flash, request, session, abort, jsonify
from extensions import db, bcrypt, login_manager
from flask_login import login_user, logout_user, login_required, current_user, UserMixin, LoginManager, AnonymousUserMixin
from forms import ConsultaDeudaForm, PagoForm, LoginForm, ProductoForm, DeudaForm, ProductoDeudaForm, ClienteForm, DeudaForm, ProductoDeudaForm, ChangePasswordForm, EmpresaForm,EmptyForm, CheckoutForm
from models import Cliente, Producto, Deuda, ProductoDeuda
from config import Config
from sqlalchemy import select
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
import time
from google.cloud.firestore_v1 import Increment
from google.cloud.firestore_v1 import DocumentReference
from datetime import datetime, timezone
import os
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
from io import BytesIO
import base64
import tempfile

# Configuración de Firebase usando variable de entorno
if not firebase_admin._apps:
    encoded_key = os.environ.get('FIREBASE_SERVICE_ACCOUNT_BASE64')
    
    if encoded_key:
        # Decodificar y crear archivo temporal
        decoded_key = base64.b64decode(encoded_key)
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(decoded_key)
            temp_path = temp_file.name
        
        cred = credentials.Certificate(temp_path)
        firebase_admin.initialize_app(cred)
    else:
        # Para desarrollo local
        cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred)

db_firestore = firestore.client()

app = Flask(__name__)
app.config.from_object(Config)

login_manager.init_app(app)
login_manager.login_view = 'login'

from google.cloud.firestore_v1 import Increment

# Función para generar IDs secuenciales
def get_next_sequence(collection_name):
    counter_ref = db_firestore.collection('counters').document(collection_name)
    try:
        @firestore.transactional
        def update_counter(transaction):
            snapshot = counter_ref.get(transaction=transaction)
            if snapshot.exists:
                seq = snapshot.get('seq') + 1
                transaction.update(counter_ref, {'seq': seq})
                return seq
            else:
                transaction.set(counter_ref, {'seq': 1})
                return 1
        return update_counter(db_firestore.transaction())
    except Exception as e:
        print(f"Error getting sequence: {e}")
        return int(time.time())


# Configurar LoginManager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Definir la clase UsuarioFirebase
class UsuarioFirebase(UserMixin):
    def __init__(self, user_data, user_id):
        self.user_data = user_data
        self.id = user_id
    
    @property
    def username(self):
        return self.user_data.get('username', '')
    
    @property
    def es_admin(self):
        return self.user_data.get('es_admin', False)

# Clase personalizada para usuarios anónimos
class AnonymousUser(AnonymousUserMixin):
    @property
    def username(self):
        return "Invitado"
    
    @property
    def es_admin(self):
        return False

login_manager.anonymous_user = AnonymousUser

@login_manager.user_loader
def load_user(user_id):
    doc_ref = db_firestore.collection('usuarios').document(user_id)
    doc = doc_ref.get()
    if doc.exists:
        user_data = doc.to_dict()
        return UsuarioFirebase(user_data, user_id)
    return None
    
@property
def username(self):
    return self.user_data['username']
    
@property
def password(self):
    return self.user_data['password']
    
@property
def es_admin(self):
    return self.user_data.get('es_admin', True)

@app.route('/categorias/<categoria>')
def productos_por_categoria(categoria):
    # Obtener productos con stock
    productos = []
    
    if categoria == 'todos':
        query = db_firestore.collection('productos').where(filter=FieldFilter('cantidad', '>', 0))
    else:
        query = db_firestore.collection('productos')\
            .where(filter=FieldFilter('categoria', '==', categoria))\
            .where(filter=FieldFilter('cantidad', '>', 0))
    
    for doc in query.stream():
        producto = doc.to_dict()
        producto['id'] = doc.id
        productos.append(producto)
    
    # Obtener todas las categorías únicas
    categorias = set()
    categorias_query = db_firestore.collection('productos').select(['categoria']).stream()
    for doc in categorias_query:
        if 'categoria' in doc.to_dict():
            categorias.add(doc.to_dict()['categoria'])
    
    return render_template('index.html', productos=productos, 
                          categorias=sorted(categorias), 
                          categoria_actual=categoria,
                          form=ConsultaDeudaForm())

# Modifica la función index() para que incluya las categorías
@app.route('/', methods=['GET', 'POST'])
def index():
    # Obtener productos con stock
    productos = []
    query = db_firestore.collection('productos').where(filter=FieldFilter('cantidad', '>', 0))
    
    for doc in query.stream():
        producto = doc.to_dict()
        producto['id'] = doc.id
        productos.append(producto)
    
    # Obtener todas las categorías únicas
    categorias = set()
    categorias_query = db_firestore.collection('productos').select(['categoria']).stream()
    for doc in categorias_query:
        if 'categoria' in doc.to_dict():
            categorias.add(doc.to_dict()['categoria'])
    
    return render_template('index.html', productos=productos, 
                          categorias=sorted(categorias), 
                          categoria_actual='todos',
                          form=ConsultaDeudaForm())

@app.route('/tienda', endpoint='tienda_page')
def tienda():
    # Obtener productos con stock
    productos = []
    query = db_firestore.collection('productos').where(filter=FieldFilter('cantidad', '>', 0))
    
    for doc in query.stream():
        producto = doc.to_dict()
        producto['id'] = doc.id
        productos.append(producto)
    
    return render_template('tienda.html', productos=productos)

@app.route('/pagar/<string:deuda_id>', methods=['GET', 'POST'])
def pagar_deuda(deuda_id):
    deuda_ref = db_firestore.collection('deudas').document(deuda_id)
    deuda = deuda_ref.get().to_dict()
    
    if not deuda:
        abort(404)
        
    form = PagoForm()
    
    if form.validate_on_submit():
        # Crear pago
        pago_data = {
            'deuda_id': deuda_id,
            'referencia': form.referencia.data,
            'banco_origen': form.banco_origen.data,
            'monto_bs': form.monto_bs.data,
            'monto_usd': form.monto_usd.data,
            'fecha': datetime.utcnow()
        }
        db_firestore.collection('pagos').add(pago_data)
        
        # Actualizar estado de deuda
        deuda_ref.update({'estado': 'pagada'})
        
        flash('Pago registrado exitosamente', 'success')
        return redirect(url_for('index'))
    
    return render_template('pagar.html', deuda=deuda, form=form)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    form = LoginForm()
    if form.validate_on_submit():
        try:
            # Buscar usuario en Firestore usando FieldFilter
            query = db_firestore.collection('usuarios').where(
                filter=FieldFilter('username', '==', form.username.data)
            ).limit(1)
            
            docs = query.stream()
            user_doc = next(docs, None)
            
            if user_doc:
                user_data = user_doc.to_dict()
                if bcrypt.check_password_hash(user_data['password'], form.password.data):
                    user = UsuarioFirebase(user_data, user_doc.id)
                    login_user(user)
                    return redirect(url_for('index'))
            
            flash('Usuario o contraseña incorrectos', 'danger')
        except Exception as e:
            print(f"Login error: {e}")
            flash('Error en el sistema, intente nuevamente', 'danger')
    
    return render_template('login.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        try:
            user_ref = db_firestore.collection('usuarios').document(current_user.id)
            user_doc = user_ref.get()
            
            if user_doc.exists:
                user_data = user_doc.to_dict()
                if bcrypt.check_password_hash(user_data['password'], form.old_password.data):
                    hashed_password = bcrypt.generate_password_hash(form.new_password.data).decode('utf-8')
                    user_ref.update({'password': hashed_password})
                    flash('Contraseña actualizada exitosamente', 'success')
                    return redirect(url_for('dashboard'))
                else:
                    flash('Contraseña actual incorrecta', 'danger')
            else:
                flash('Usuario no encontrado', 'danger')
        except Exception as e:
            print(f"Error al cambiar contraseña: {e}")
            flash('Error al cambiar la contraseña', 'danger')
    
    return render_template('change_password.html', form=form)

@app.route('/dashboard')
@login_required
def dashboard():
    # Obtener productos
    productos = []
    productos_ref = db_firestore.collection('productos').stream()
    for doc in productos_ref:
        prod = doc.to_dict()
        prod['id'] = doc.id
        productos.append(prod)
    
    # Obtener productos con bajo stock (<5 unidades)
    productos_bajo_stock = []
    for prod in productos:
        try:
            if int(prod.get('cantidad', 0)) < 5:
                productos_bajo_stock.append(prod)
        except (ValueError, TypeError):
            pass
    
    # Obtener clientes
    clientes = []
    clientes_ref = db_firestore.collection('clientes').stream()
    for doc in clientes_ref:
        cliente = doc.to_dict()
        cliente['id'] = doc.id
        clientes.append(cliente)
    
    # Calcular estadísticas
    total_stock = 0
    total_value = 0.0
    for prod in productos:
        try:
            cantidad = int(prod.get('cantidad', 0))
            precio = float(prod.get('precio', 0.0))
        except (ValueError, TypeError):
            cantidad = 0
            precio = 0.0
        total_stock += cantidad
        total_value += cantidad * precio
    
    # Obtener top 5 deudores con deudas más antiguas
    top_deudores = []
    deudas_query = db_firestore.collection('deudas') \
        .where('estado', '==', 'pendiente') \
        .order_by('fecha') \
        .limit(5) \
        .stream()
    
    for deuda_doc in deudas_query:
        deuda_data = deuda_doc.to_dict()
        cliente_ref = deuda_data.get('cliente_id')
        
        # Manejar diferentes tipos de referencia
        if isinstance(cliente_ref, DocumentReference):
            cliente_id = cliente_ref.id
        elif isinstance(cliente_ref, str):
            cliente_id = cliente_ref
        else:
            continue
        
        cliente_doc = db_firestore.collection('clientes').document(cliente_id).get()
        if cliente_doc.exists:
            cliente = cliente_doc.to_dict()
            saldo = obtener_saldo_pendiente(deuda_doc.id)
            top_deudores.append({
                'cedula': cliente.get('cedula', ''),
                'fecha': deuda_data.get('fecha'),
                'saldo': saldo
            })
    
    # Calcular total pendiente por cobrar
    total_pendiente = 0.0
    deudas_pendientes = db_firestore.collection('deudas').where('estado', '==', 'pendiente').stream()
    for deuda_doc in deudas_pendientes:
        total_pendiente += obtener_saldo_pendiente(deuda_doc.id)
    
    return render_template('dashboard.html',  
                           productos_bajo_stock=productos_bajo_stock,
                           clientes=clientes[:3],
                           total_stock=total_stock,
                           total_value=total_value,
                           deudas_pendientes=len(list(deudas_pendientes)),
                           top_deudores=top_deudores,
                           total_pendiente=total_pendiente,
                           form=EmptyForm())


@app.route('/registrar_cliente', methods=['GET', 'POST'])
@login_required
def registrar_cliente():
    form = ClienteForm()
    if form.validate_on_submit():  # Esto ahora siempre será True si se envía el formulario
        try:
            # Obtener próximo ID secuencial
            next_id = get_next_sequence('clientes')
            cliente_data = {
                'nombre': request.form.get('nombre', ''),  # Usar valor por defecto
                'cedula': request.form.get('cedula', ''),
                'direccion': request.form.get('direccion', ''),
                'telefono': request.form.get('telefono', ''),
                'email': request.form.get('email', '')
            }
            
            # Actualizar contador
            counter_ref = db_firestore.collection('counters').document('clientes')
            counter_ref.update({'seq': Increment(1)})
            
            # Guardar cliente con ID secuencial
            db_firestore.collection('clientes').document(str(next_id)).set(cliente_data)
            
            flash('Cliente registrado exitosamente', 'success')
            return redirect(url_for('listar_clientes'))
        except Exception as e:
            print(f"Error al registrar cliente: {e}")
            flash('Error al registrar el cliente', 'danger')
    return render_template('registrar_cliente.html', form=form)

@app.route('/clientes')
@login_required
def listar_clientes():
    try:
        # Usar FieldFilter explícito
        query = db_firestore.collection('clientes')
        docs = query.stream()
        
        clientes = []
        for doc in docs:
            cliente_data = doc.to_dict()
            cliente_data['id'] = doc.id
            clientes.append(cliente_data)
        
        return render_template('clientes.html', clientes=clientes, form=EmptyForm())
    except Exception as e:
        print(f"Error listing clients: {e}")
        flash('Error al cargar los clientes', 'danger')
        return redirect(url_for('dashboard'))

@app.route('/editar_cliente/<string:id>', methods=['POST'])
@login_required
def editar_cliente(id):
    doc_ref = db_firestore.collection('clientes').document(id)
    doc = doc_ref.get()
    
    if not doc.exists:
        abort(404)
    
    # Actualizar el cliente con valores por defecto para campos vacíos
    doc_ref.update({
        'nombre': request.form.get('nombre', ''),
        'cedula': request.form.get('cedula', ''),
        'direccion': request.form.get('direccion', ''),
        'telefono': request.form.get('telefono', ''),
        'email': request.form.get('email', '')
    })
    flash('Cliente actualizado exitosamente', 'success')
    return redirect(url_for('listar_clientes'))

@app.route('/eliminar_cliente/<string:id>', methods=['POST'])
@login_required
def eliminar_cliente(id):
    db_firestore.collection('clientes').document(id).delete()
    flash('Cliente eliminado correctamente', 'success')
    return redirect(url_for('listar_clientes'))

@app.route('/registrar_producto', methods=['POST'])
@login_required
def registrar_producto():
    try:
        # Obtener datos del formulario
        nombre = request.form.get('nombre')
        cantidad = int(request.form.get('cantidad'))
        precio = float(request.form.get('precio'))
        categoria = request.form.get('categoria')
        imagen_url = request.form.get('imagen_url')
        
        # Validar datos básicos
        if not nombre or cantidad < 0 or precio <= 0:
            return jsonify({
                'success': False,
                'errors': {
                    'nombre': ['Nombre es requerido'] if not nombre else [],
                    'cantidad': ['Cantidad no puede ser negativa'] if cantidad < 0 else [],
                    'precio': ['Precio debe ser mayor a cero'] if precio <= 0 else []
                }
            }), 400
        
        # Crear documento con ID secuencial
        next_id = get_next_sequence('productos')
        producto_data = {
            'nombre': nombre,
            'cantidad': cantidad,
            'precio': precio,
            'categoria': categoria,
            'imagen_url': imagen_url,
            'fecha': datetime.utcnow()
        }
        
        # Guardar producto
        db_firestore.collection('productos').document(str(next_id)).set(producto_data)
        
        # Actualizar contador
        db_firestore.collection('counters').document('productos').update({'seq': Increment(1)})
        
        return jsonify({'success': True})
    
    except Exception as e:
        print(f"Error al registrar producto: {e}")
        return jsonify({
            'success': False,
            'message': 'Error al registrar el producto'
        }), 500

@app.route('/productos')
@login_required
def listar_productos():
    try:
        productos = []
        docs = db_firestore.collection('productos').stream()
        
        for doc in docs:
            prod = doc.to_dict()
            prod['id'] = doc.id
            productos.append(prod)
        
        # Ordenar por ID numérico
        productos.sort(key=lambda x: int(x['id']))
        
        return render_template('productos.html', productos=productos, form=EmptyForm())
    except Exception as e:
        print(f"Error al listar productos: {e}")
        flash('Error al cargar productos', 'danger')
        return redirect(url_for('dashboard'))

@app.route('/editar_producto/<string:id>', methods=['POST'])
@login_required
def editar_producto(id):
    try:
        # Validar que el producto exista
        doc_ref = db_firestore.collection('productos').document(id)
        if not doc_ref.get().exists:
            return jsonify({'success': False, 'message': 'Producto no encontrado'}), 404
        
        # Obtener datos del formulario
        nombre = request.form.get('nombre')
        cantidad = int(request.form.get('cantidad'))
        precio = float(request.form.get('precio'))
        categoria = request.form.get('categoria')
        imagen_url = request.form.get('imagen_url')
        
        # Validar datos básicos
        if not nombre or cantidad < 0 or precio <= 0:
            return jsonify({
                'success': False,
                'errors': {
                    'nombre': ['Nombre es requerido'] if not nombre else [],
                    'cantidad': ['Cantidad no puede ser negativa'] if cantidad < 0 else [],
                    'precio': ['Precio debe ser mayor a cero'] if precio <= 0 else []
                }
            }), 400
        
        # Actualizar el producto
        doc_ref.update({
            'nombre': nombre,
            'cantidad': cantidad,
            'precio': precio,
            'categoria': categoria,
            'imagen_url': imagen_url
        })
        
        return jsonify({'success': True})
    
    except Exception as e:
        print(f"Error al actualizar producto: {e}")
        return jsonify({
            'success': False,
            'message': 'Error al actualizar el producto'
        }), 500

def calcular_precio_sin_iva(precio_con_iva):
    return precio_con_iva * 100 / 116

@app.route('/registrar_deuda', methods=['GET', 'POST'])
@login_required
def registrar_deuda():
    print("=== INICIANDO REGISTRAR_DEUDA ===")
    
    # Obtener clientes ORDENADOS alfabéticamente por nombre
    clientes = []
    clientes_docs = db_firestore.collection('clientes').stream()
    for doc in clientes_docs:
        cliente = doc.to_dict()
        cliente['id'] = doc.id
        clientes.append(cliente)
    
    # Ordenar clientes alfabéticamente por nombre
    clientes.sort(key=lambda x: x['nombre'].lower())

    # Obtener productos
    productos = []
    productos_docs = db_firestore.collection('productos').stream()
    for doc in productos_docs:
        prod = doc.to_dict()
        prod['id'] = doc.id
        productos.append(prod)

    # Crear formularios
    deuda_form = DeudaForm()
    producto_form = ProductoDeudaForm()
    
    # Poblar opciones del formulario
    deuda_form.cliente_id.choices = [('', 'Seleccione un cliente')] + [(c['id'], f"{c['nombre']}") for c in clientes]
    producto_form.producto_id.choices = [('', 'Seleccione un producto')] + [(p['id'], p['nombre']) for p in productos]
    
    # Inicializar lista de productos en sesión
    if 'productos_deuda' not in session:
        session['productos_deuda'] = []
    
    # Manejar selección de cliente
    if request.method == 'POST' and 'select_cliente' in request.form:
        print("=== MANEJANDO SELECCIÓN DE CLIENTE ===")
        cliente_id = request.form.get('cliente_id')
        print(f"Cliente ID recibido: {cliente_id}")
        
        if cliente_id:
            # Guardar en sesión
            session['cliente_seleccionado'] = cliente_id
            flash('Cliente seleccionado correctamente', 'success')
            print(f"Cliente guardado en sesión: {cliente_id}")
        else:
            flash('Error al seleccionar cliente', 'danger')
        
        return redirect(url_for('registrar_deuda'))
    
    # Cargar cliente seleccionado de la sesión si existe
    cliente_seleccionado_id = session.get('cliente_seleccionado')
    if cliente_seleccionado_id:
        deuda_form.cliente_id.data = cliente_seleccionado_id
        print(f"Cliente cargado de sesión: {cliente_seleccionado_id}")
    
    # Manejar agregar producto
    if request.method == 'POST' and 'agregar' in request.form:
        print("=== MANEJANDO AGREGAR PRODUCTO ===")
        
        # Verificar que hay un cliente seleccionado
        if not session.get('cliente_seleccionado'):
            flash('Debe seleccionar un cliente primero', 'danger')
            return redirect(url_for('registrar_deuda'))
        
        selected_product_id = request.form.get('producto_id')
        cantidad_str = request.form.get('cantidad')
        
        print(f"Producto ID: {selected_product_id}, Cantidad: {cantidad_str}")
        
        if not selected_product_id or not cantidad_str:
            flash('Debe seleccionar un producto y cantidad', 'danger')
            return redirect(url_for('registrar_deuda'))
        
        try:
            cantidad = int(cantidad_str)
        except ValueError:
            flash('Cantidad debe ser un número válido', 'danger')
            return redirect(url_for('registrar_deuda'))

        # Verificar stock disponible
        producto_ref = db_firestore.collection('productos').document(selected_product_id)
        producto_doc = producto_ref.get()
        
        if producto_doc.exists:
            producto_data = producto_doc.to_dict()
            stock_actual = producto_data.get('cantidad', 0)
            
            print(f"Stock actual: {stock_actual}, Cantidad solicitada: {cantidad}")
            
            if cantidad > stock_actual:
                flash(f'No hay suficiente stock. Disponible: {stock_actual}', 'danger')
                return redirect(url_for('registrar_deuda'))
            
            # Agregar producto si hay stock suficiente
            session['productos_deuda'].append({
                'producto_id': selected_product_id,
                'cantidad': cantidad
            })
            session.modified = True
            print(f"Producto agregado. Total productos: {len(session['productos_deuda'])}")
            flash('Producto agregado correctamente', 'success')
        else:
            flash('Producto no encontrado', 'danger')
        
        return redirect(url_for('registrar_deuda'))
    
    # Manejar guardar deuda COMPLETA
    if request.method == 'POST' and 'guardar' in request.form:
        print("=== MANEJANDO GUARDAR DEUDA COMPLETA ===")
        
        # Validar que hay un cliente seleccionado
        if not session.get('cliente_seleccionado'):
            flash('Debe seleccionar un cliente', 'danger')
            print("ERROR: No hay cliente seleccionado")
            return redirect(url_for('registrar_deuda'))

        # Validar que hay productos en la deuda
        if not session.get('productos_deuda'):
            flash('Debe agregar al menos un producto a la deuda', 'danger')
            print("ERROR: No hay productos en la deuda")
            return redirect(url_for('registrar_deuda'))
        
        try:
            # Obtener próximo ID secuencial
            next_id = get_next_sequence('deudas')
            print(f"ID de deuda generado: {next_id}")
            
            # Obtener cliente de la sesión
            cliente_id = session['cliente_seleccionado']
            cliente_ref = db_firestore.collection('clientes').document(cliente_id)
            cliente_doc = cliente_ref.get()
            
            if not cliente_doc.exists:
                flash('Cliente no encontrado', 'danger')
                print(f"ERROR: Cliente {cliente_id} no encontrado")
                return redirect(url_for('registrar_deuda'))
            
            cliente_data = cliente_doc.to_dict()
            print(f"Cliente: {cliente_data.get('nombre')}")
            
            # Crear datos de deuda
            deuda_data = {
                'cliente_id': cliente_ref,
                'cliente_nombre': cliente_data.get('nombre', ''),
                'cliente_cedula': cliente_data.get('cedula', ''),
                'fecha': datetime.utcnow(),
                'estado': 'pendiente'
            }
            
            # Guardar deuda en Firestore
            deuda_ref = db_firestore.collection('deudas').document(str(next_id))
            deuda_ref.set(deuda_data)
            print(f"Deuda guardada en Firestore: {next_id}")
            
            # Guardar productos asociados
            productos_guardados = 0
            for item in session['productos_deuda']:
                producto_id = item['producto_id']
                cantidad = item['cantidad']
                
                producto_ref = db_firestore.collection('productos').document(producto_id)
                
                producto_deuda_data = {
                    'deuda_id': str(next_id),
                    'producto_id': producto_id,
                    'cantidad': cantidad
                }
                
                # Guardar relación producto-deuda
                db_firestore.collection('productos_deuda').add(producto_deuda_data)
                productos_guardados += 1
                
                # Actualizar inventario (reducir cantidad)
                producto_ref.update({
                    'cantidad': firestore.Increment(-cantidad)
                })
                print(f"Producto {producto_id} - cantidad reducida en {cantidad}")
            
            print(f"Total productos guardados: {productos_guardados}")
            
            # Actualizar contador
            counter_ref = db_firestore.collection('counters').document('deudas')
            counter_ref.update({'seq': Increment(1)})
            
            # Limpiar sesión COMPLETAMENTE
            session.pop('productos_deuda', None)
            session.pop('cliente_seleccionado', None)
            session.modified = True
            
            print("=== DEUDA REGISTRADA EXITOSAMENTE - REDIRIGIENDO A CONSULTAR_DEUDAS ===")
            flash('Deuda registrada exitosamente', 'success')
            return redirect(url_for('consultar_deudas'))
            
        except Exception as e:
            print(f"ERROR al registrar deuda: {e}")
            import traceback
            traceback.print_exc()
            flash('Error al registrar la deuda', 'danger')
    
    # Obtener detalles de productos para mostrar
    productos_en_deuda = []
    for item in session.get('productos_deuda', []):
        producto_ref = db_firestore.collection('productos').document(str(item['producto_id']))
        producto_doc = producto_ref.get()
        if producto_doc.exists:
            producto = producto_doc.to_dict()
            precio = producto.get('precio', 0)
            cantidad = item['cantidad']
            subtotal = precio * cantidad
            
            productos_en_deuda.append({
                'id': item['producto_id'],
                'nombre': producto.get('nombre', ''),
                'cantidad': cantidad,
                'precio': precio,
                'subtotal': subtotal
            })
        else:
            productos_en_deuda.append({
                'id': item['producto_id'],
                'nombre': 'Producto eliminado',
                'cantidad': item['cantidad'],
                'precio': 0,
                'subtotal': 0
            })
    
    # Calcular el total de la deuda
    total_deuda = sum(item['subtotal'] for item in productos_en_deuda)
    
    # Obtener información del cliente seleccionado para la plantilla
    cliente_seleccionado_info = None
    if cliente_seleccionado_id:
        for cliente in clientes:
            if cliente['id'] == cliente_seleccionado_id:
                cliente_seleccionado_info = cliente
                break
    
    print(f"Renderizando template - Cliente seleccionado: {cliente_seleccionado_id}, Productos: {len(productos_en_deuda)}")
    
    return render_template('registrar_deuda.html', 
                          deuda_form=deuda_form,
                          producto_form=producto_form,
                          productos_deuda=productos_en_deuda,
                          total=total_deuda,
                          clientes=clientes,
                          cliente_seleccionado_id=cliente_seleccionado_id,
                          cliente_seleccionado_info=cliente_seleccionado_info,
                          form=EmptyForm())

@app.route('/consultar_deudas')
@login_required
def consultar_deudas():
    try:
        # Obtener parámetros de filtro
        estado_filtro = request.args.get('estado', 'todos')
        cedula_filtro = request.args.get('cedula', '').strip().lower()
        
        # Construir consulta base
        deudas_ref = db_firestore.collection('deudas')
        
        # Aplicar filtro de estado si no es 'todos'
        if estado_filtro != 'todos':
            deudas_ref = deudas_ref.where(filter=FieldFilter('estado', '==', estado_filtro))
        
        # Ejecutar consulta inicial
        deudas_docs = deudas_ref.stream()
        
        deudas = []
        for deuda_doc in deudas_docs:
            deuda_data = deuda_doc.to_dict()
            deuda = {
                'id': deuda_doc.id,
                'estado': deuda_data.get('estado', 'pendiente'),
                'fecha': deuda_data.get('fecha', None),
                'cliente_cedula': deuda_data.get('cliente_cedula', '')
            }
            
            # Obtener referencia al cliente
            cliente_ref = deuda_data.get('cliente_id')
            cliente_id = None
            if isinstance(cliente_ref, DocumentReference):
                cliente_id = cliente_ref.id
            elif isinstance(cliente_ref, str):
                cliente_id = cliente_ref
            else:
                continue
                
            # Obtener nombre del cliente
            cliente_doc = db_firestore.collection('clientes').document(cliente_id).get()
            if cliente_doc.exists:
                deuda['cliente_nombre'] = cliente_doc.to_dict().get('nombre', '')
            else:
                deuda['cliente_nombre'] = 'Cliente eliminado'
            deuda['cliente_id'] = cliente_id

            # Calcular total de la deuda
            total = 0.0
            productos_query = db_firestore.collection('productos_deuda')\
                .where('deuda_id', '==', deuda_doc.id).stream()
            
            for prod_doc in productos_query:
                prod_data = prod_doc.to_dict()
                producto_id = prod_data.get('producto_id', '')
                cantidad = prod_data.get('cantidad', 0)
                
                if producto_id:
                    if isinstance(producto_id, DocumentReference):
                        producto_ref = producto_id
                    elif isinstance(producto_id, str):
                        producto_ref = db_firestore.collection('productos').document(producto_id)
                    else:
                        continue
                    
                    producto_doc = producto_ref.get()
                    if producto_doc.exists:
                        producto = producto_doc.to_dict()
                        precio = producto.get('precio', 0.0)
                        total += precio * cantidad

            deuda['total'] = total
            
            # Calcular saldo pendiente
            saldo = total
            pagos_query = db_firestore.collection('pagos_parciales')\
                .where('deuda_id', '==', deuda_doc.id).stream()
            
            for pago_doc in pagos_query:
                pago_data = pago_doc.to_dict()
                monto = pago_data.get('monto_usd', 0.0)
                saldo -= monto

            deuda['saldo_pendiente'] = saldo
            
            # Aplicar filtro de cedula (si se proporcionó)
            if cedula_filtro and cedula_filtro not in deuda['cliente_cedula'].lower():
                continue
                
            deudas.append(deuda)
        
        # Ordenar por fecha descendente
        deudas.sort(key=lambda x: x['fecha'] if x['fecha'] else datetime.min, reverse=True)
        
        return render_template('consultar_deudas.html', deudas=deudas, 
                               estado_filtro=estado_filtro, cedula_filtro=cedula_filtro,
                          form=EmptyForm())
    except Exception as e:
        import traceback
        traceback.print_exc()
        flash(f'Error al cargar deudas: {str(e)}', 'danger')
        return redirect(url_for('dashboard'))

@app.route('/eliminar_producto_temp/<int:index>', methods=['POST'])
@login_required
def eliminar_producto_temp(index):
    if 'productos_deuda' in session and 0 <= index < len(session['productos_deuda']):
        session['productos_deuda'].pop(index)
        session.modified = True
    return redirect(url_for('registrar_deuda'))

@app.route('/consulta_deuda_cliente', methods=['GET', 'POST'])
def consulta_deuda_cliente():
    form = ConsultaDeudaForm()
    if form.validate_on_submit():
        nombre = form.nombre.data.strip()  # Normalizar entrada
        
        # Buscar cliente (insensible a mayúsculas)
        clientes_ref = db_firestore.collection('clientes')
        clientes = []
        for doc in clientes_ref.stream():
            cliente_data = doc.to_dict()
            if cliente_data.get('nombre', '') == nombre:
                cliente = cliente_data
                cliente['id'] = doc.id
                clientes.append(cliente)
        
        if not clientes:
            flash('Cliente no encontrado', 'info')
            return redirect(url_for('index'))
        
        cliente = clientes[0]  # Tomar el primer cliente coincidente
        cliente_id = cliente['id']
        
        # Obtener todas las deudas del cliente
        deudas_ref = db_firestore.collection('deudas')
        deudas_info = []
        total_pendiente = 0.0
        
        for deuda_doc in deudas_ref.stream():
            deuda_data = deuda_doc.to_dict()
            deuda_cliente_ref = deuda_data.get('cliente_id')
            
            # Normalizar la referencia al cliente
            deuda_cliente_id = None
            if isinstance(deuda_cliente_ref, DocumentReference):
                deuda_cliente_id = deuda_cliente_ref.id
            elif isinstance(deuda_cliente_ref, str):
                deuda_cliente_id = deuda_cliente_ref
            else:
                continue
                
            if deuda_cliente_id != cliente_id:
                continue
                
            # Procesar la deuda
            deuda_info = {
                'id': deuda_doc.id,
                'fecha': deuda_data.get('fecha'),
                'estado': deuda_data.get('estado', 'pendiente'),
                'productos': [],
                'pagos_parciales': [],
                'total': 0.0,
                'saldo_pendiente': 0.0
            }
            
            # Obtener productos de la deuda
            productos_query = db_firestore.collection('productos_deuda')\
                .where('deuda_id', '==', deuda_doc.id).stream()
            
            for prod_doc in productos_query:
                prod_data = prod_doc.to_dict()
                producto_id = prod_data.get('producto_id', '')
                cantidad = prod_data.get('cantidad', 0)
                
                if not producto_id:
                    continue
                    
                # Obtener detalles del producto
                if isinstance(producto_id, DocumentReference):
                    producto_ref = producto_id
                elif isinstance(producto_id, str):
                    producto_ref = db_firestore.collection('productos').document(producto_id)
                else:
                    continue
                    
                producto_doc = producto_ref.get()
                if not producto_doc.exists:
                    continue
                    
                producto = producto_doc.to_dict()
                precio = producto.get('precio', 0.0)
                try:
                    precio = float(precio)
                except (TypeError, ValueError):
                    precio = 0.0
                    
                subtotal = precio * cantidad
                deuda_info['productos'].append({
                    'producto': producto,
                    'cantidad': cantidad,
                    'precio': precio,
                    'subtotal': subtotal
                })
                deuda_info['total'] += subtotal
            
            # Obtener pagos parciales
            pagos_query = db_firestore.collection('pagos_parciales')\
                .where('deuda_id', '==', deuda_doc.id).stream()
            
            total_pagos = 0.0
            for pago_doc in pagos_query:
                pago_data = pago_doc.to_dict()
                monto = pago_data.get('monto_usd', 0.0)
                try:
                    monto = float(monto)
                except (TypeError, ValueError):
                    monto = 0.0
                    
                total_pagos += monto
                
                # Convertir fecha si es necesario
                fecha_pago = pago_data.get('fecha')
                if hasattr(fecha_pago, 'timestamp'):
                    pago_data['fecha'] = datetime.fromtimestamp(fecha_pago.timestamp())
                
                deuda_info['pagos_parciales'].append(pago_data)
            
            deuda_info['saldo_pendiente'] = deuda_info['total'] - total_pagos
            
            if deuda_info['estado'] == 'pendiente':
                total_pendiente += deuda_info['saldo_pendiente']
            
            deudas_info.append(deuda_info)
        
        # Ordenar deudas por fecha (más reciente primero)
        deudas_info.sort(key=lambda x: x['fecha'] if x['fecha'] else datetime.min, reverse=True)
        
    # Separar deudas en pendientes y pagadas
        deudas_pendientes = []
        deudas_pagadas = []
        for deuda in deudas_info:
            if deuda['estado'] == 'pendiente':
                deudas_pendientes.append(deuda)
            else:
                deudas_pagadas.append(deuda)
        
        return render_template('consulta_deuda_cliente.html', 
                            cliente=cliente, 
                            deudas_pendientes=deudas_pendientes,
                            deudas_pagadas=deudas_pagadas,
                            total_pendiente=total_pendiente,
                            form=EmptyForm())
    
    return redirect(url_for('index'))
            

@app.route('/editar_deuda/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_deuda(id):
    deuda = db.session.get(Deuda, id) or abort(404)
    
    # Obtener todos los clientes
    clientes = db.session.execute(select(Cliente)).scalars().all()
    
    # Crear formulario
    deuda_form = DeudaForm()
    deuda_form.cliente_id.choices = [(c.id, f"{c.nombre} ({c.cedula})") for c in clientes]
    deuda_form.cliente_id.data = deuda.cliente_id
    
    # Formulario para agregar productos
    producto_form = ProductoDeudaForm()
    producto_form.producto_id.choices = [(p.id, p.nombre) for p in Producto.query.all()]
    
    # Inicializar la sesión si no existe
    if 'productos_deuda' not in session:
        session['productos_deuda'] = []
        # Cargar productos existentes
        for producto_deuda in deuda.productos:
            session['productos_deuda'].append({
                'producto_id': producto_deuda.producto_id,
                'cantidad': producto_deuda.cantidad
            })
    
    if producto_form.agregar.data and producto_form.validate():
        session['productos_deuda'].append({
            'producto_id': producto_form.producto_id.data,
            'cantidad': producto_form.cantidad.data
        })
        session.modified = True
        return redirect(url_for('editar_deuda', id=id))
    
    if deuda_form.guardar.data and deuda_form.validate():
     new_cliente_id = deuda_form.cliente_id.data
     new_cliente = db.session.get(Cliente, new_cliente_id)
    
    if not new_cliente:
        flash('Cliente no encontrado', 'danger')
        return redirect(url_for('editar_deuda', id=id))
    
    # Actualizar cliente asociado
    deuda.cliente_id = new_cliente_id
    deuda.cliente_cedula = new_cliente.cedula
    
    # Eliminar productos antiguos
    ProductoDeuda.query.filter_by(deuda_id=deuda.id).delete()
    
    # Agregar nuevos productos
    for item in session['productos_deuda']:
        producto_deuda = ProductoDeuda(
            deuda_id=deuda.id,
            producto_id=item['producto_id'],
            cantidad=item['cantidad']
        )
        db.session.add(producto_deuda)
    
     # Limpiar sesión
    session.pop('productos_deuda', None)
    db.session.commit()
    

    flash('Deuda actualizada exitosamente', 'success')
    return redirect(url_for('consultar_deudas'))

@app.route('/gestion_deudas/<string:cliente_id>', methods=['GET'])
@login_required
def gestion_deudas(cliente_id):
    # Obtener cliente
    cliente_ref = db_firestore.collection('clientes').document(cliente_id)
    cliente_doc = cliente_ref.get()
    
    if not cliente_doc.exists:
        abort(404)
    
    cliente = cliente_doc.to_dict()
    cliente['id'] = cliente_id
    
    # Obtener todas las deudas del cliente
    query = db_firestore.collection('deudas').where(
    filter=FieldFilter('cliente_id', '==', cliente_ref))
    
    deudas_pendientes = []
    deudas_pagadas = []
    
    for deuda_doc in query.stream():
        deuda_data = deuda_doc.to_dict()
        deuda = {
            'id': deuda_doc.id,
            'estado': deuda_data.get('estado', 'pendiente'),
            'cliente_cedula': deuda_data.get('cliente_cedula', '')
        }
        
        # Manejo de fechas
        fecha = deuda_data.get('fecha')
        if fecha:
            if hasattr(fecha, 'timestamp'):
                deuda['fecha'] = datetime.fromtimestamp(fecha.timestamp(), tz=timezone.utc).replace(tzinfo=None)
            else:
                deuda['fecha'] = fecha
        else:
            deuda['fecha'] = None
        
        # Obtener productos de la deuda
        deuda['productos'] = []
        total_sin_iva = 0.0
        productos_query = db_firestore.collection('productos_deuda').where('deuda_id', '==', deuda_doc.id).stream()
        
        for prod_doc in productos_query:
            prod_data = prod_doc.to_dict()
            producto_id = prod_data.get('producto_id', '')
            cantidad = prod_data.get('cantidad', 0)
            
            if producto_id:
                if isinstance(producto_id, DocumentReference):
                    producto_ref = producto_id
                elif isinstance(producto_id, str):
                    producto_ref = db_firestore.collection('productos').document(producto_id)
                else:
                    continue
                
                producto_doc = producto_ref.get()
                if producto_doc.exists:
                    producto = producto_doc.to_dict()
                    precio_con_iva = producto.get('precio', 0.0)
                    precio_sin_iva = calcular_precio_sin_iva(precio_con_iva)
                    subtotal_sin_iva = precio_sin_iva * cantidad
                    total_sin_iva += subtotal_sin_iva
                    
                    deuda['productos'].append({
                        'producto': producto,
                        'cantidad': cantidad,
                        'precio_sin_iva': precio_sin_iva,
                        'subtotal_sin_iva': subtotal_sin_iva
                    })
        
        # Calcular IVA y total con IVA
        iva = total_sin_iva * 0.16
        total_con_iva = total_sin_iva + iva
        
        deuda['subtotal_sin_iva'] = total_sin_iva
        deuda['iva'] = iva
        deuda['total_con_iva'] = total_con_iva
        
        # Obtener pagos parciales
        deuda['pagos_parciales'] = []
        saldo_pendiente = total_con_iva
        pagos_query = db_firestore.collection('pagos_parciales').where('deuda_id', '==', deuda_doc.id).stream()
        
        for pago_doc in pagos_query:
            pago_data = pago_doc.to_dict()
            monto = pago_data.get('monto_usd', 0.0)
            try:
                monto = float(monto)
            except (TypeError, ValueError):
                monto = 0.0
            saldo_pendiente -= monto
            
            # Manejo de fechas para pagos
            if 'fecha' in pago_data and hasattr(pago_data['fecha'], 'timestamp'):
                pago_data['fecha'] = datetime.fromtimestamp(pago_data['fecha'].timestamp(), tz=timezone.utc).replace(tzinfo=None)
            deuda['pagos_parciales'].append(pago_data)
        
        deuda['saldo_pendiente'] = saldo_pendiente
        
        # Separar deudas en pendientes y pagadas
        if deuda['estado'] == 'pendiente':
            deudas_pendientes.append(deuda)
        else:
            deudas_pagadas.append(deuda)
    
    return render_template('gestion_deudas.html', 
                          cliente=cliente, 
                          deudas_pendientes=deudas_pendientes,
                          deudas_pagadas=deudas_pagadas)

@app.route('/marcar_pagada/<string:deuda_id>', methods=['POST'])
@login_required
def marcar_pagada(deuda_id):
    deuda_ref = db_firestore.collection('deudas').document(deuda_id)
    if deuda_ref.get().exists:
        deuda_ref.update({'estado': 'pagada'})
        flash('Deuda marcada como pagada', 'success')
    else:
        flash('Deuda no encontrada', 'danger')
    return redirect(url_for('consultar_deudas'))

@app.route('/eliminar_producto/<string:id>', methods=['POST'])
@login_required
def eliminar_producto(id):
    try:
        doc_ref = db_firestore.collection('productos').document(id)
        if doc_ref.get().exists:
            doc_ref.delete()
            flash('Producto eliminado correctamente', 'success')
        else:
            flash('Producto no encontrado', 'danger')
    except Exception as e:
        print(f"Error al eliminar producto: {e}")
        flash('Error al eliminar el producto', 'danger')
    return redirect(url_for('listar_productos'))


@app.route('/registrar_pago_parcial/<string:deuda_id>', methods=['POST'])
@login_required
def registrar_pago_parcial(deuda_id):
    try:
        # Obtener datos del formulario
        monto = float(request.form.get('monto'))
        monto = round(monto, 2)
        descripcion = request.form.get('descripcion', 'Pago parcial')
        cliente_id = request.form.get('cliente_id')
        
        # Validar cliente_id
        if not cliente_id:
            flash('Cliente no especificado', 'danger')
            return redirect(url_for('dashboard'))
        
        saldo_pendiente = obtener_saldo_pendiente(deuda_id)
        saldo_pendiente = round(saldo_pendiente, 2)
        
        # Validaciones
        if monto <= 0:
            flash('El monto debe ser mayor a cero', 'danger')
            return redirect(url_for('gestion_deudas', cliente_id=cliente_id))
        
        # Validar que el monto no exceda el saldo pendiente (con tolerancia)
        if monto > saldo_pendiente + 0.01:  # Pequeña tolerancia para decimales
            flash(f'El monto no puede exceder el saldo pendiente (${saldo_pendiente:.2f})', 'danger')
            return redirect(url_for('gestion_deudas', cliente_id=cliente_id))
        
        # Ajustar monto si es casi igual al saldo pendiente (para evitar problemas de decimales)
        if abs(monto - saldo_pendiente) < 0.01:
            monto = saldo_pendiente
        
        # Crear pago parcial
        pago_data = {
            'deuda_id': deuda_id,
            'monto_usd': monto,
            'descripcion': descripcion,
            'fecha': datetime.utcnow()
        }
        db_firestore.collection('pagos_parciales').add(pago_data)

        # Verificar si la deuda queda saldada (con tolerancia para decimales)
        nuevo_saldo = saldo_pendiente - monto
        if nuevo_saldo <= 0.01:  # Tolerancia de 1 centavo
            # Si el saldo es muy pequeño, consideramos la deuda pagada
            db_firestore.collection('deudas').document(deuda_id).update({
                'estado': 'pagada'
            })
            flash('¡Deuda completamente saldada!', 'success')
        else:
            flash('Pago parcial registrado exitosamente', 'success')
        
        return redirect(url_for('gestion_deudas', cliente_id=cliente_id))
    except Exception as e:
        print(f"Error al registrar pago parcial: {e}")
        flash('Error al registrar el pago', 'danger')
        return redirect(url_for('dashboard'))

def obtener_saldo_pendiente(deuda_id):
    """Calcula el saldo pendiente de una deuda con precisión decimal"""
    try:
        # Calcular total de la deuda
        total = 0.0
        productos_query = db_firestore.collection('productos_deuda').where(
            filter=FieldFilter('deuda_id', '==', deuda_id)).stream()
        
        for prod_doc in productos_query:
            prod_data = prod_doc.to_dict()
            producto_id = prod_data.get('producto_id', '')
            cantidad = float(prod_data.get('cantidad', 0))
            
            if producto_id:
                if isinstance(producto_id, DocumentReference):
                    producto_ref = producto_id
                elif isinstance(producto_id, str):
                    producto_ref = db_firestore.collection('productos').document(producto_id)
                else:
                    continue
                
                producto_doc = producto_ref.get()
                if producto_doc.exists:
                    producto = producto_doc.to_dict()
                    precio = float(producto.get('precio', 0.0))
                    total += precio * cantidad
        
        # Restar pagos parciales
        pagos_query = db_firestore.collection('pagos_parciales').where(
            filter=FieldFilter('deuda_id', '==', deuda_id)).stream()
        
        for pago_doc in pagos_query:
            pago_data = pago_doc.to_dict()
            monto = float(pago_data.get('monto_usd', 0.0))
            total -= monto
        
        return round(total, 2)
    
    except Exception as e:
        print(f"Error al calcular saldo pendiente: {e}")
        return 0.0

@app.route('/eliminar_deuda/<string:deuda_id>', methods=['POST'])
@login_required
def eliminar_deuda(deuda_id):
    try:
        # Verificar que la deuda existe
        deuda_ref = db_firestore.collection('deudas').document(deuda_id)
        deuda_doc = deuda_ref.get()
        
        if not deuda_doc.exists:
            flash('Deuda no encontrada', 'danger')
            return redirect(url_for('consultar_deudas'))
        
        # Obtener productos asociados a la deuda para restaurar stock
        productos_deuda_query = db_firestore.collection('productos_deuda').where('deuda_id', '==', deuda_id).stream()
        
        for prod_doc in productos_deuda_query:
            prod_data = prod_doc.to_dict()
            producto_id = prod_data.get('producto_id')
            cantidad = prod_data.get('cantidad', 0)
            
            # Restaurar stock del producto
            if producto_id:
                if isinstance(producto_id, DocumentReference):
                    producto_ref = producto_id
                elif isinstance(producto_id, str):
                    producto_ref = db_firestore.collection('productos').document(producto_id)
                else:
                    continue
                
                producto_ref.update({
                    'cantidad': firestore.Increment(cantidad)
                })
            
            # Eliminar el registro de producto_deuda
            prod_doc.reference.delete()
        
        # Eliminar pagos parciales asociados
        pagos_query = db_firestore.collection('pagos_parciales').where('deuda_id', '==', deuda_id).stream()
        for pago_doc in pagos_query:
            pago_doc.reference.delete()
        
        # Finalmente eliminar la deuda
        deuda_ref.delete()
        
        flash('Deuda eliminada exitosamente', 'success')
        
    except Exception as e:
        print(f"Error al eliminar deuda: {e}")
        flash('Error al eliminar la deuda', 'danger')
    
    return redirect(url_for('consultar_deudas'))

@app.route('/api/producto/<string:producto_id>')
@login_required
def api_get_producto(producto_id):
    try:
        producto_ref = db_firestore.collection('productos').document(producto_id)
        producto_doc = producto_ref.get()
        
        if producto_doc.exists:
            producto = producto_doc.to_dict()
            return jsonify({
                'id': producto_id,
                'nombre': producto.get('nombre', ''),
                'cantidad': producto.get('cantidad', 0),
                'precio': producto.get('precio', 0)
            })
        else:
            return jsonify({'error': 'Producto no encontrado'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/procesar_pedido/<string:pedido_id>', methods=['POST'])
@login_required
def procesar_pedido(pedido_id):
    accion = request.form.get('accion')
    
    if accion == 'aceptar':
        # Convertir pedido en deuda
        pedido_ref = db_firestore.collection('pedidos').document(pedido_id)
        pedido_doc = pedido_ref.get()
        if not pedido_doc.exists:
            flash('Pedido no encontrado', 'danger')
            return redirect(url_for('listar_pedidos'))
        
        pedido_data = pedido_doc.to_dict()
        
        # Buscar cliente o crear uno nuevo
        clientes_ref = db_firestore.collection('clientes')
        clientes_query = clientes_ref.where('nombre', '==', pedido_data['cliente_nombre']).limit(1).stream()
        cliente_doc = next(clientes_query, None)
        
        if cliente_doc:
            cliente_id = cliente_doc.id
            cliente_ref = clientes_ref.document(cliente_id)
        else:
            # Crear nuevo cliente
            cliente_data = {
                'nombre': pedido_data['cliente_nombre'],
                'cedula': '',
                'direccion': pedido_data['cliente_direccion'],
                'telefono': pedido_data['cliente_telefono'],
                'email': pedido_data['cliente_email']
            }
            next_id = get_next_sequence('clientes')
            cliente_ref = clientes_ref.document(str(next_id))
            cliente_ref.set(cliente_data)
            cliente_id = cliente_ref.id
        
        # Crear la deuda
        deuda_data = {
            'cliente_id': cliente_ref,
            'cliente_cedula': cliente_ref.get().get('cedula') or '',
            'fecha': datetime.utcnow(),
            'estado': 'pendiente'
        }
        next_deuda_id = get_next_sequence('deudas')
        deuda_ref = db_firestore.collection('deudas').document(str(next_deuda_id))
        deuda_ref.set(deuda_data)
        
        # Agregar productos a la deuda
        items_query = db_firestore.collection('items_pedido').where('pedido_id', '==', pedido_id).stream()
        for item_doc in items_query:
            item = item_doc.to_dict()
            producto_deuda_data = {
                'deuda_id': str(next_deuda_id),
                'producto_id': item['producto_id'],
                'cantidad': item['cantidad']
            }
            db_firestore.collection('productos_deuda').add(producto_deuda_data)
        
        # Cambiar estado del pedido
        pedido_ref.update({'estado': 'completado'})
        
        flash('Pedido convertido en deuda exitosamente', 'success')
        return redirect(url_for('consultar_deudas'))
    
    elif accion == 'cancelar':
        # Restaurar stock y eliminar pedido
        pedido_ref = db_firestore.collection('pedidos').document(pedido_id)
        items_query = db_firestore.collection('items_pedido').where('pedido_id', '==', pedido_id).stream()
        
        for item_doc in items_query:
            item = item_doc.to_dict()
            producto_ref = db_firestore.collection('productos').document(item['producto_id'])
            producto_ref.update({'cantidad': firestore.Increment(item['cantidad'])})
            db_firestore.collection('items_pedido').document(item_doc.id).delete()
        
        pedido_ref.delete()
        flash('Pedido cancelado y stock restaurado', 'success')
        return redirect(url_for('listar_pedidos'))
    
    elif accion == 'modificar':
        return redirect(url_for('editar_pedido', pedido_id=pedido_id))
    
    flash('Acción no válida', 'danger')
    return redirect(url_for('listar_pedidos'))

@app.route('/editar_pedido/<string:pedido_id>', methods=['GET', 'POST'])
@login_required
def editar_pedido(pedido_id):
    # Obtener pedido
    pedido_ref = db_firestore.collection('pedidos').document(pedido_id)
    pedido_doc = pedido_ref.get()
    if not pedido_doc.exists:
        abort(404)
    
    pedido = pedido_doc.to_dict()
    pedido['id'] = pedido_id
    
    # Obtener productos
    productos = []
    productos_ref = db_firestore.collection('productos').stream()
    for doc in productos_ref:
        prod = doc.to_dict()
        prod['id'] = doc.id
        productos.append(prod)
    
    # Obtener items del pedido
    items = []
    items_query = db_firestore.collection('items_pedido').where('pedido_id', '==', pedido_id).stream()
    for item_doc in items_query:
        item = item_doc.to_dict()
        item['id'] = item_doc.id
        items.append(item)
    
    # Calcular total
    total = sum(item['precio'] * item['cantidad'] for item in items)
    
    if request.method == 'POST':
        # Lógica para agregar nuevo producto
        producto_id = request.form.get('producto_id')
        cantidad = int(request.form.get('cantidad', 1))
        
        if producto_id:
            producto_ref = db_firestore.collection('productos').document(producto_id)
            producto_doc = producto_ref.get()
            if producto_doc.exists:
                producto = producto_doc.to_dict()
                
                # Verificar stock
                if cantidad > producto['cantidad']:
                    flash(f'No hay suficiente stock. Disponible: {producto["cantidad"]}', 'danger')
                    return redirect(url_for('editar_pedido', pedido_id=pedido_id))
                
                # Agregar item
                item_data = {
                    'pedido_id': pedido_id,
                    'producto_id': producto_id,
                    'producto_nombre': producto['nombre'],
                    'precio': producto['precio'],
                    'cantidad': cantidad
                }
                db_firestore.collection('items_pedido').add(item_data)
                
                # Actualizar stock
                producto_ref.update({'cantidad': firestore.Increment(-cantidad)})
                
                flash('Producto agregado al pedido', 'success')
                return redirect(url_for('editar_pedido', pedido_id=pedido_id))
    
    return render_template('editar_pedido.html', 
                          pedido=pedido, 
                          items=items, 
                          productos=productos,
                          total=total)

@app.route('/actualizar_item_pedido/<string:item_id>', methods=['POST'])
@login_required
def actualizar_item_pedido(item_id):
    nueva_cantidad = int(request.form.get('cantidad'))
    
    item_ref = db_firestore.collection('items_pedido').document(item_id)
    item_doc = item_ref.get()
    if not item_doc.exists:
        flash('Ítem no encontrado', 'danger')
        return redirect(url_for('listar_pedidos'))
    
    item = item_doc.to_dict()
    producto_ref = db_firestore.collection('productos').document(item['producto_id'])
    producto_doc = producto_ref.get()
    
    if not producto_doc.exists:
        flash('Producto no encontrado', 'danger')
        return redirect(url_for('editar_pedido', pedido_id=item['pedido_id']))
    
    producto = producto_doc.to_dict()
    diferencia = nueva_cantidad - item['cantidad']
    
    # Verificar stock
    if diferencia > 0 and diferencia > producto['cantidad']:
        flash(f'No hay suficiente stock. Disponible: {producto["cantidad"]}', 'danger')
        return redirect(url_for('editar_pedido', pedido_id=item['pedido_id']))
    
    # Actualizar stock
    producto_ref.update({'cantidad': firestore.Increment(-diferencia)})
    
    # Actualizar item
    item_ref.update({'cantidad': nueva_cantidad})
    
    flash('Cantidad actualizada', 'success')
    return redirect(url_for('editar_pedido', pedido_id=item['pedido_id']))

@app.route('/eliminar_item_pedido/<string:item_id>', methods=['POST'])
@login_required
def eliminar_item_pedido(item_id):
    item_ref = db_firestore.collection('items_pedido').document(item_id)
    item_doc = item_ref.get()
    if not item_doc.exists:
        flash('Ítem no encontrado', 'danger')
        return redirect(url_for('listar_pedidos'))
    
    item = item_doc.to_dict()
    
    # Restaurar stock
    producto_ref = db_firestore.collection('productos').document(item['producto_id'])
    producto_ref.update({'cantidad': firestore.Increment(item['cantidad'])})
    
    # Eliminar item
    item_ref.delete()
    
    flash('Producto eliminado del pedido', 'success')
    return redirect(url_for('editar_pedido', pedido_id=item['pedido_id']))

@app.route('/descargar_factura/<string:deuda_id>')
@login_required
def descargar_factura(deuda_id):
    # Obtener deuda
    deuda_ref = db_firestore.collection('deudas').document(deuda_id)
    deuda_doc = deuda_ref.get()
    if not deuda_doc.exists:
        abort(404)
    
    deuda_data = deuda_doc.to_dict()
    
    # Obtener cliente
    cliente_ref = deuda_data['cliente_id']
    cliente_id = cliente_ref.id if isinstance(cliente_ref, DocumentReference) else cliente_ref
    cliente_doc = db_firestore.collection('clientes').document(cliente_id).get()
    cliente = cliente_doc.to_dict() if cliente_doc.exists else {}
    
    # Obtener productos de la deuda
    productos = []
    productos_query = db_firestore.collection('productos_deuda').where('deuda_id', '==', deuda_id).stream()
    for prod_doc in productos_query:
        prod_data = prod_doc.to_dict()
        producto_id = prod_data['producto_id']
        
        if isinstance(producto_id, DocumentReference):
            producto_ref = producto_id
        elif isinstance(producto_id, str):
            producto_ref = db_firestore.collection('productos').document(producto_id)
        else:
            continue
        
        producto_doc = producto_ref.get()
        if producto_doc.exists:
            producto = producto_doc.to_dict()
            precio_sin_iva = calcular_precio_sin_iva(producto['precio'])
            productos.append({
                'nombre': producto['nombre'],
                'cantidad': prod_data['cantidad'],
                'precio_sin_iva': precio_sin_iva,
                'subtotal_sin_iva': precio_sin_iva * prod_data['cantidad']
            })
    
    # Calcular totales
    subtotal = sum(p['subtotal_sin_iva'] for p in productos)
    iva = subtotal * 0.16
    total = subtotal + iva
    
    # Crear PDF
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    
    # Encabezado
    p.setFont("Helvetica-Bold", 16)
    p.drawString(72, height - 72, "Factura")
    p.setFont("Helvetica", 12)
    p.drawString(72, height - 100, f"Deuda ID: {deuda_id}")
    p.drawString(72, height - 120, f"Fecha: {deuda_data['fecha'].strftime('%d/%m/%Y')}")
    
    # Información del cliente
    p.drawString(72, height - 160, "Cliente:")
    p.drawString(72, height - 180, cliente.get('nombre', ''))
    p.drawString(72, height - 200, f"Cédula: {cliente.get('cedula', '')}")
    p.drawString(72, height - 220, f"Dirección: {cliente.get('direccion', '')}")
    p.drawString(72, height - 240, f"Teléfono: {cliente.get('telefono', '')}")
    
    # Tabla de productos
    data = [['Producto', 'Cantidad', 'Precio Unitario (sin IVA)', 'Subtotal (sin IVA)']]
    for producto in productos:
        data.append([
            producto['nombre'],
            str(producto['cantidad']),
            f"${producto['precio_sin_iva']:.2f}",
            f"${producto['subtotal_sin_iva']:.2f}"
        ])
    
    # Añadir filas de totales
    data.append(['', '', 'Sub Total:', f"${subtotal:.2f}"])
    data.append(['', '', 'IVA (16%):', f"${iva:.2f}"])
    data.append(['', '', 'Total:', f"${total:.2f}"])
    
    table = Table(data, colWidths=[200, 80, 120, 100])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 12),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,-4), colors.beige),
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        ('SPAN', (0,-3), (1,-3)),  # Unir celdas para totales
        ('SPAN', (0,-2), (1,-2)),
        ('SPAN', (0,-1), (1,-1)),
        ('ALIGN', (-2,-3), (-1,-1), 'RIGHT'),
        ('FONTNAME', (-2,-3), (-1,-1), 'Helvetica-Bold'),
    ]))
    
    table.wrapOn(p, width, height)
    table.drawOn(p, 72, height - 400)
    
    p.save()
    
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"factura_{deuda_id}.pdf", mimetype='application/pdf')

@app.route('/mi_cuenta', methods=['GET', 'POST'])
@login_required
def mi_cuenta():
    # Obtener información existente de la empresa
    empresa_ref = db_firestore.collection('empresa').document('info')
    empresa_doc = empresa_ref.get()
    empresa_data = empresa_doc.to_dict() if empresa_doc.exists else None
    
    form = EmpresaForm()
    
    # Cargar datos existentes en el formulario
    if request.method == 'GET' and empresa_data:
        form.nombre.data = empresa_data.get('nombre', '')
        form.direccion.data = empresa_data.get('direccion', '')
        form.telefono.data = empresa_data.get('telefono', '')
        form.facebook.data = empresa_data.get('facebook', '')
        form.instagram.data = empresa_data.get('instagram', '')
        form.twitter.data = empresa_data.get('twitter', '')
        form.logo_url.data = empresa_data.get('logo_url', '')
    
    if form.validate_on_submit():
        # Guardar/actualizar información
        empresa_data = {
            'nombre': form.nombre.data,
            'direccion': form.direccion.data,
            'telefono': form.telefono.data,
            'facebook': form.facebook.data,
            'instagram': form.instagram.data,
            'twitter': form.twitter.data,
            'logo_url': form.logo_url.data
        }
        
        empresa_ref.set(empresa_data)
        flash('Información de la empresa actualizada correctamente', 'success')
        return redirect(url_for('mi_cuenta'))
    
    return render_template('mi_cuenta.html', form=form, empresa=empresa_data)

# Función para inyectar datos de la empresa en todas las plantillas
@app.context_processor
def inject_empresa():
    empresa_ref = db_firestore.collection('empresa').document('info')
    empresa_doc = empresa_ref.get()
    if empresa_doc.exists:
        return {'empresa': empresa_doc.to_dict()}
    return {'empresa': None}

# Ruta para la tienda (nueva página principal)
@app.route('/tienda')
def tienda():
    # Obtener productos con stock
    productos = []
    query = db_firestore.collection('productos').where('cantidad', '>', 0)
    
    for doc in query.stream():
        producto = doc.to_dict()
        producto['id'] = doc.id
        productos.append(producto)
    
    return render_template('tienda.html', productos=productos)

@app.route('/cart')
def view_cart():
    cart = session.get('cart', {})
    total = 0
    
    # Calcular el total y añadir información adicional
    cart_items = []
    for product_id, item in cart.items():
        # Obtener información actualizada del producto
        product_ref = db_firestore.collection('productos').document(product_id)
        product_doc = product_ref.get()
        
        if product_doc.exists:
            product = product_doc.to_dict()
            item['name'] = product['nombre']
            item['price'] = float(product['precio'])
            item['image'] = product.get('imagen_url', '')
            item['max_quantity'] = product['cantidad']  # Stock disponible
            
            # Calcular subtotal
            item['subtotal'] = item['price'] * item['quantity']
            total += item['subtotal']
            
            cart_items.append(item)
    
    return render_template('cart.html', cart_items=cart_items, total=total)

# Ruta para añadir al carrito
@app.route('/add_to_cart/<string:product_id>', methods=['POST'])
def add_to_cart(product_id):
    quantity = int(request.form.get('quantity', 1))
    
    # Obtener información del producto
    product_ref = db_firestore.collection('productos').document(product_id)
    product_doc = product_ref.get()
    
    if not product_doc.exists:
        flash('Producto no encontrado', 'danger')
        return redirect(url_for('index'))
    
    product = product_doc.to_dict()
    
    # Verificar stock
    if quantity > product['cantidad']:
        flash(f'No hay suficiente stock. Disponible: {product["cantidad"]}', 'danger')
        return redirect(url_for('index'))
    
    # Inicializar carrito en sesión
    if 'cart' not in session:
        session['cart'] = {}
    
    # Añadir o actualizar producto en carrito
    if product_id in session['cart']:
        new_quantity = session['cart'][product_id]['quantity'] + quantity
        if new_quantity > product['cantidad']:
            flash(f'No puedes agregar más de {product["cantidad"]} unidades', 'danger')
            return redirect(url_for('index'))
        
        session['cart'][product_id]['quantity'] = new_quantity
    else:
        session['cart'][product_id] = {
            'quantity': quantity,
            'name': product['nombre'],
            'price': float(product['precio']),
            'image': product.get('imagen_url', '')
        }
    
    session.modified = True
    flash(f'Producto {product["nombre"]} añadido al carrito', 'success')
    session['cart_updated'] = datetime.utcnow().isoformat()
    return redirect(url_for('index'))

# Ruta para actualizar cantidad en el carrito
@app.route('/update_cart_quantity/<string:product_id>', methods=['POST'])
def update_cart_quantity(product_id):
    new_quantity = int(request.form.get('quantity', 1))
    
    if 'cart' not in session or product_id not in session['cart']:
        flash('Producto no encontrado en el carrito', 'danger')
        return redirect(url_for('view_cart'))
    
    # Obtener información actual del producto
    product_ref = db_firestore.collection('productos').document(product_id)
    product_doc = product_ref.get()
    
    if not product_doc.exists:
        flash('Producto no encontrado', 'danger')
        return redirect(url_for('view_cart'))
    
    product = product_doc.to_dict()
    
    # Verificar stock
    if new_quantity > product['cantidad']:
        flash(f'No hay suficiente stock. Disponible: {product["cantidad"]}', 'danger')
        return redirect(url_for('view_cart'))
    
    session['cart'][product_id]['quantity'] = new_quantity
    session.modified = True
    session['cart_updated'] = datetime.utcnow().isoformat()
    return redirect(url_for('view_cart'))

# Ruta para eliminar del carrito
@app.route('/remove_from_cart/<string:product_id>', methods=['POST'])
def remove_from_cart(product_id):
    if 'cart' in session and product_id in session['cart']:
        del session['cart'][product_id]
        session.modified = True
        flash('Producto eliminado del carrito', 'success')
        session['cart_updated'] = datetime.utcnow().isoformat()
    return redirect(url_for('view_cart'))

# Ruta para obtener contador del carrito
@app.route('/cart_count')
def cart_count():
    count = 0
    if 'cart' in session:
        count = sum(item['quantity'] for item in session['cart'].values())
    return jsonify({'count': count})

@app.route('/cart_sidebar_partial')
def cart_sidebar_partial():
    cart = session.get('cart', {})
    total = 0
    cart_items = []
    
    for product_id, item in cart.items():
        product_ref = db_firestore.collection('productos').document(product_id)
        product_doc = product_ref.get()
        
        if product_doc.exists:
            product = product_doc.to_dict()
            item['name'] = product['nombre']
            item['price'] = float(product['precio'])
            item['image'] = product.get('imagen_url', '')
            item['subtotal'] = item['price'] * item['quantity']
            total += item['subtotal']
            cart_items.append({
                'id': product_id,
                'name': item['name'],
                'price': item['price'],
                'quantity': item['quantity'],
                'subtotal': item['subtotal'],
                'image': item['image']
            })
    
    return render_template('partials/cart_sidebar.html', 
                          cart_items=cart_items, 
                          total=total)


@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    # Obtener el carrito de la sesión
    cart = session.get('cart', {})
    
    if not cart:
        flash('Tu carrito está vacío', 'warning')
        return redirect(url_for('index'))
    
    # Obtener información actualizada de los productos
    cart_items = []
    total = 0
    
    for product_id, item in cart.items():
        product_ref = db_firestore.collection('productos').document(product_id)
        product_doc = product_ref.get()
        
        if product_doc.exists:
            product = product_doc.to_dict()
            # Crear un nuevo objeto con la información necesaria
            cart_item = {
                'id': product_id,
                'name': item['name'],
                'price': float(product['precio']),
                'quantity': item['quantity'],
                'subtotal': float(product['precio']) * item['quantity'],
                'image': item.get('image', '')
            }
            total += cart_item['subtotal']
            cart_items.append(cart_item)
    
    form = CheckoutForm()
    
    if form.validate_on_submit():
        try:
            # Crear un nuevo pedido en Firestore
            pedido_data = {
                'cliente_nombre': form.nombre.data,
                'cliente_direccion': form.direccion.data,
                'cliente_telefono': form.telefono.data,
                'cliente_email': form.email.data,
                'notas': form.notas.data,
                'total': total,
                'estado': 'pendiente',
                'fecha': datetime.utcnow()
            }
            
            # Guardar el pedido
            pedido_ref = db_firestore.collection('pedidos').document()
            pedido_ref.set(pedido_data)
            pedido_id = pedido_ref.id
            
            # Guardar los items del pedido
            for item in cart_items:
                item_data = {
                    'pedido_id': pedido_id,
                    'producto_id': item['id'],
                    'producto_nombre': item['name'],
                    'precio': item['price'],
                    'cantidad': item['quantity']
                }
                db_firestore.collection('items_pedido').add(item_data)
                
                # Actualizar el stock
                product_ref = db_firestore.collection('productos').document(item['id'])
                product_ref.update({
                    'cantidad': firestore.Increment(-item['quantity'])
                })
            
            # Vaciar el carrito
            session.pop('cart', None)
            
            flash('Pedido realizado con éxito. ¡Gracias!', 'success')
            return redirect(url_for('index'))
        except Exception as e:
            print(f"Error al realizar el pedido: {e}")
            flash('Error al procesar el pedido', 'danger')
    
    return render_template('checkout.html', form=form, total=total, cart_items=cart_items)



@app.route('/pedidos')
@login_required
def listar_pedidos():
    # Obtener todos los pedidos
    pedidos = []
    pedidos_ref = db_firestore.collection('pedidos').order_by('fecha', direction=firestore.Query.DESCENDING)
    
    for pedido_doc in pedidos_ref.stream():
        pedido = pedido_doc.to_dict()
        pedido['id'] = pedido_doc.id
        pedidos.append(pedido)
    
    # Crear un formulario vacío
    from forms import EmptyForm  # Importa un formulario vacío
    return render_template('pedidos.html', pedidos=pedidos, form=EmptyForm())

@app.route('/pedido/<string:pedido_id>')
@login_required
def ver_pedido(pedido_id):
    # Obtener el pedido
    pedido_ref = db_firestore.collection('pedidos').document(pedido_id)
    pedido_doc = pedido_ref.get()
    
    if not pedido_doc.exists:
        abort(404)
    
    pedido = pedido_doc.to_dict()
    pedido['id'] = pedido_id
    
    # Obtener los items del pedido
    items = []
    items_query = db_firestore.collection('items_pedido').where(
    filter=FieldFilter('pedido_id', '==', pedido_id))
    
    for item_doc in items_query.stream():
        item = item_doc.to_dict()
        item['id'] = item_doc.id
        items.append(item)
    
    return render_template('detalle_pedido.html', pedido=pedido, items=items)

from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from io import BytesIO
from datetime import datetime

def exportar_deudas_pdf_filtrado():
    """
    Exporta todas las deudas a PDF con opción de filtro por estado.
    Deudas pendientes en naranja, pagadas en verde, ordenadas por estado.
    """
    try:
        # Obtener parámetro de filtro
        filtro = request.args.get('filtro', 'todas').lower()  # 'todas', 'pendientes', 'pagadas'
        
        # Obtener todas las deudas
        db = get_db()
        cursor = db.cursor(dictionary=True)
        
        query = "SELECT * FROM deudas"
        cursor.execute(query)
        deudas = cursor.fetchall()
        
        # Filtrar deudas según el parámetro
        if filtro == 'pendientes':
            deudas = [d for d in deudas if d.get('saldo_pendiente', 0) > 0]
        elif filtro == 'pagadas':
            deudas = [d for d in deudas if d.get('saldo_pendiente', 0) <= 0]
        
        # Ordenar: pendientes primero, luego pagadas
        deudas_pendientes = [d for d in deudas if d.get('saldo_pendiente', 0) > 0]
        deudas_pagadas = [d for d in deudas if d.get('saldo_pendiente', 0) <= 0]
        deudas_ordenadas = deudas_pendientes + deudas_pagadas
        
        # Crear PDF con Platypus
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, 
            pagesize=letter,
            rightMargin=30,
            leftMargin=30,
            topMargin=40,
            bottomMargin=30,
            title="Reporte de Deudas"
        )
        
        # Estilos
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=16,
            textColor=colors.HexColor('#1a1a1a'),
            spaceAfter=6,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        )
        
        normal_style = ParagraphStyle(
            'Normal',
            fontSize=9,
            alignment=TA_CENTER,
            valign='MIDDLE'
        )
        
        # Contenido del documento
        story = []
        
        # Título y fecha
        fecha_actual = datetime.now().strftime('%d/%m/%Y %H:%M')
        story.append(Paragraph("REPORTE DE DEUDAS", title_style))
        story.append(Paragraph(f"Generado: {fecha_actual}", styles['Normal']))
        story.append(Paragraph(f"Filtro: {filtro.upper()}", styles['Normal']))
        story.append(Spacer(1, 0.2*inch))
        
        # Construir tabla de deudas
        data = [['ID', 'Cliente', 'Cédula', 'Fecha', 'Monto', 'Saldo', 'Estado']]
        
        for deuda in deudas_ordenadas:
            estado_pago = 'PAGADA' if deuda.get('saldo_pendiente', 0) <= 0 else 'PENDIENTE'
            
            data.append([
                str(deuda.get('id_deuda', '')),
                str(deuda.get('nombre_cliente', '')),
                str(deuda.get('cedula_cliente', '')),
                deuda.get('fecha', '').strftime('%d/%m/%Y') if hasattr(deuda.get('fecha'), 'strftime') else str(deuda.get('fecha', '')),
                f"${deuda.get('monto_total', 0):.2f}",
                f"${deuda.get('saldo_pendiente', 0):.2f}",
                estado_pago
            ])
        
        # Crear tabla con estilos
        table = Table(data, colWidths=[0.6*inch, 1.5*inch, 1*inch, 0.8*inch, 0.8*inch, 0.8*inch, 0.8*inch])
        
        table_style = [
            # Encabezado
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
            ('ALIGN', (0, 1), (-1, -1), 'CENTER'),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('ROWPADDING', (0, 1), (-1, -1), 6),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]
        
        # Colorear filas según estado (pendiente = naranja, pagada = verde)
        row_num = 1
        for deuda in deudas_ordenadas:
            is_pendiente = deuda.get('saldo_pendiente', 0) > 0
            
            if is_pendiente:
                # Naranja claro para pendientes
                background_color = colors.HexColor('#FFE0B2')
                text_color = colors.HexColor('#E65100')
            else:
                # Verde claro para pagadas
                background_color = colors.HexColor('#C8E6C9')
                text_color = colors.HexColor('#2E7D32')
            
            table_style.append(
                ('BACKGROUND', (0, row_num), (-1, row_num), background_color)
            )
            table_style.append(
                ('TEXTCOLOR', (0, row_num), (-1, row_num), text_color)
            )
            table_style.append(
                ('FONTNAME', (0, row_num), (-1, row_num), 'Helvetica-Bold')
            )
            row_num += 1
        
        table.setStyle(TableStyle(table_style))
        story.append(table)
        
        # Resumen
        story.append(Spacer(1, 0.3*inch))
        total_deudas = len(deudas_ordenadas)
        total_monto = sum(d.get('monto_total', 0) for d in deudas_ordenadas)
        total_saldo = sum(d.get('saldo_pendiente', 0) for d in deudas_ordenadas)
        
        resumen = f"""
        <b>RESUMEN:</b><br/>
        Total de deudas: {total_deudas}<br/>
        Monto total: ${total_monto:.2f}<br/>
        Saldo pendiente: ${total_saldo:.2f}
        """
        story.append(Paragraph(resumen, styles['Normal']))
        
        # Generar PDF
        doc.build(story)
        buffer.seek(0)
        
        # Nombre del archivo
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"deudas_{filtro}_{timestamp}.pdf"
        
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
    
    except Exception as e:
        return {"error": f"Error al generar PDF: {str(e)}"}, 500


if __name__ == '__main__':
    # Configuración para acceso en red local:
    app.run(
        host='0.0.0.0',  # Escucha en todas las interfaces de red
        port=5001,        # Puerto (puedes cambiarlo si necesitas)
        debug=True        # Solo para desarrollo!
    )
