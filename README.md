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
   
2. **Instalar dependencias con Poetry:**
   ```bash
   poetry install
   
3. **Ejecutar el servidor de desarrollo:**
   ```bash
   python -m poetry run uvicorn app.main:app --reload
   
4. **Documentación interactiva (Swagger UI):**

     Accede a http://127.0.0.1:8000/docs desde tu navegador.
