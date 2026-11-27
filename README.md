# 🌐 LifeHub - Backend API

Servidor RESTful central para la plataforma **LifeHub**, diseñado para la gestión personal y familiar unificada.

## 🚀 Tecnologías
* **Lenguaje:** Python 3.14+
* **Framework:** FastAPI
* **ORM:** SQLAlchemy
* **Gestor de Dependencias:** Poetry
* **Base de Datos:** PostgreSQL (vía Docker)

## 📌 Módulos Implementados
- [x] **Gestión de Usuarios y Roles:** Control de acceso básico (`admin`, `editor`, `guest`).
- [x] **Control Financiero:** Creación y seguimiento de ingresos, gastos fijos, semanales y extras.
- [ ] **Gestor de Vehículos:** *(En desarrollo)*
- [ ] **Calendario & Recordatorios:** *(En desarrollo)*
- [ ] **Bóveda de Contraseñas Cifrada:** *(En desarrollo)*

## 🛠️ Requisitos e Instalación Local

1. **Iniciar Base de Datos en Docker:**
   ```bash
   docker-compose up -d
   ```

2. **Instalar dependencias con Poetry:**
   ```bash
   poetry install
   ```

3. **Ejecutar el servidor de desarrollo:**
   ```bash
   python -m poetry run uvicorn app.main:app --reload
   ```

4. **Documentación interactiva (Swagger UI):**
   Accede a `http://127.0.0.1:8000/docs` desde tu navegador.

## Bancos (beta): Enable Banking para uso personal

La integración consulta Enable Banking para Revolut, BBVA y Sabadell; la lista de entidades y países procede del proveedor. Está pensada para una aplicación **restricted** con cuentas propias autorizadas (*whitelist*). No incluye sincronización periódica ni efectos sobre los presupuestos: las asignaciones y reglas son únicamente informativas. Los datos reales dependen del banco y de su consentimiento.

### Registrar la aplicación y preparar las claves

1. Crea una cuenta y registra una aplicación en el [panel de Enable Banking](https://enablebanking.com/cp/applications). Selecciona el entorno adecuado. Sandbox y producción son aplicaciones distintas; en producción, activa el uso personal restringido y autoriza en el proveedor las cuentas propias que correspondan.
2. Genera una clave privada RSA y su certificado público según la [guía oficial](https://enablebanking.com/docs/api/reference/) o utiliza la generación en el navegador del panel. Sube únicamente el certificado público; conserva la clave privada fuera de Git. Si el proveedor te entrega la clave en PEM, por ejemplo, guárdala como `backend/secrets/enable-banking.pem`. El proceso debe poder leerla; restringe los permisos de acceso. El directorio `secrets/`, los PEM y otros formatos habituales de clave están excluidos de Git y del contexto de construcción Docker. No subas la clave privada ni la pongas en la base de datos o en el frontend.
3. Registra la **URL de retorno exacta**, incluido esquema, host, puerto y ruta, y configura ese mismo valor en `ENABLE_BANKING_REDIRECT_URL`:
   - **Sandbox local:** `http://localhost:5173/banks/callback`. Sandbox admite `http://localhost`.
   - **Producción:** usa una URL `https` cuyo host no sea `localhost`, por ejemplo `https://lifehub.example.com/banks/callback`, sustituyendo `lifehub.example.com` por el dominio que registres. Producción rechaza HTTP y localhost. La URL no tiene que ser accesible públicamente: si no carga tras autorizar, copia la URL completa de la barra y pégala en **Bancos → Pegar URL de retorno**. LifeHub valida el mismo `state` ligado al usuario; la autorización pendiente caduca a los 15 minutos y es de un solo uso. Si el navegador sí llega a la aplicación, `/banks/callback` completa el retorno automáticamente.
4. Copia el App ID de la aplicación. Genera además una clave Fernet independiente para cifrar identificadores de sesión en reposo, por ejemplo, en un entorno privado con `cryptography` instalado:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   Guarda la salida como secreto y no la compartas. Conserva una copia segura: si se pierde o se cambia, no se podrán descifrar ni revocar las sesiones existentes con esa clave. No reutilices la clave privada RSA para Fernet.

### Variables de entorno

En `backend/.env` (ignorado por Git) o en el entorno seguro del proceso, configura:

| Variable | Valor |
|---|---|
| `ENABLE_BANKING_APP_ID` | App ID obtenido del proveedor. |
| `ENABLE_BANKING_KEY_PATH` | Ruta a la clave RSA privada PEM legible por el backend. En Compose, si la guardaste en `backend/secrets/enable-banking.pem`, usa `/app/secrets/enable-banking.pem`; fuera de Docker usa la ruta local correspondiente. |
| `ENABLE_BANKING_REDIRECT_URL` | URL de retorno exacta registrada, como en los ejemplos anteriores. |
| `ENABLE_BANKING_ENCRYPTION_KEY` | Clave Fernet generada para cifrar los identificadores de sesión. |

`backend/.env.example` contiene marcadores vacíos, no credenciales. Compose monta `backend/` en `/app` y transmite las cuatro variables al backend. Tras configurarlas, recrea el servicio, por ejemplo `docker compose up -d --build backend`. No ejecutes ni compartas salidas de comandos que muestren el entorno o la configuración resuelta con secretos. En un despliegue HTTPS real sirve frontend y API mediante HTTPS y ajusta `CORS_ORIGINS` a los orígenes permitidos si no comparten origen. Sin configuración válida la API devuelve `configured: false` y la vista lo indica; el resto de LifeHub sigue funcionando.

### Uso y privacidad

En **Bancos (beta)** selecciona la entidad y el país devueltos por el proveedor, autoriza en el banco y sincroniza. La sincronización solicita movimientos de los últimos 90 días y pagina las respuestas; el banco puede ofrecer menos historial. La cobertura muestra únicamente los campos presentes en los datos guardados. Al renovar se crea una nueva conexión y se intenta revocar la anterior; la anterior y su historial se conservan hasta eliminarlos. Eliminar una conexión revoca su consentimiento si es posible y borra sus cuentas, movimientos y asignaciones bancarias. Las reglas se aplican manualmente a movimientos sin asignar. No se crean transacciones de presupuesto ni gastos extra, y no se marcan gastos fijos como pagados.

Las claves privadas y de cifrado no se guardan en la base de datos; los identificadores de sesión se guardan cifrados con Fernet. Las cuentas, saldos y respuestas JSON de los movimientos sí se guardan en la base de datos: protégela y limita sus copias de seguridad. Si el banco no proporciona un identificador, se deduplica con un hash de fecha, importe, concepto y cuenta; movimientos idénticos pueden confundirse. No se han incluido bancos adicionales, sincronización automática ni avisos de caducidad.

### Pruebas

Con las dependencias de desarrollo instaladas (`poetry install --no-root`), ejecuta `poetry run python -m unittest discover -s tests -p 'test*.py'`. Las pruebas bancarias usan SQLite en memoria y un proveedor simulado; no requieren credenciales bancarias ni acceso a la base real.