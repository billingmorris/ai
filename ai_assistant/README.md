# 🤖 AI Assistant — Módulo Odoo 16

Integración de múltiples proveedores de IA (ChatGPT, Claude, DeepSeek) para automatizar operaciones en Odoo 16.

---

## 📦 Instalación

```bash
# 1. Copiar el módulo a tu directorio de addons
cp -r ai_assistant /odoo/addons/

# 2. Actualizar la lista de módulos en Odoo
# Configuración > Activar modo desarrollador > Actualizar lista de Apps

# 3. Instalar el módulo
# Apps > Buscar "AI Assistant" > Instalar

# 4. Instalar dependencia Python
pip install requests
```

---

## ⚙️ Configuración Inicial

### 1. Configurar un Proveedor

Ve a **🤖 IA Assistant > Configuración > Proveedores de IA** y crea uno:

| Proveedor | Modelo por defecto | URL del API |
|-----------|-------------------|-------------|
| OpenAI (ChatGPT) | `gpt-4o` | `https://api.openai.com/v1/chat/completions` |
| Anthropic (Claude) | `claude-sonnet-4-6` | `https://api.anthropic.com/v1/messages` |
| DeepSeek | `deepseek-chat` | `https://api.deepseek.com/v1/chat/completions` |

### 2. Obtener API Keys

- **OpenAI**: https://platform.openai.com/api-keys
- **Anthropic**: https://console.anthropic.com/
- **DeepSeek**: https://platform.deepseek.com/

### 3. Probar la conexión

Desde el formulario del proveedor, haz clic en **"🔌 Probar Conexión"**.

---

## 🚀 Uso

### Desde el menú principal

**🤖 IA Assistant > Abrir Asistente**

Selecciona la acción que quieres realizar:

| Acción | Descripción |
|--------|-------------|
| 🧾 Crear Factura | Describe la factura en lenguaje natural → se crea en Odoo |
| 🛒 Crear Pedido de Venta | Describe el pedido → se crea automáticamente |
| 📧 Redactar Correo | La IA escribe el correo → se abre el compositor |
| ✏️ Redactar Texto | Cualquier texto: descripciones, contratos, notas |
| 📊 Analizar Datos | Análisis y conclusiones |
| 📋 Resumir | Resúmenes ejecutivos |
| 🌐 Traducir | Traducción a múltiples idiomas |

### Desde cualquier registro (chatter)

En facturas, pedidos, contactos, etc., usa el botón:
- **🤖 Asistente IA** — Abre el asistente en contexto del registro
- **📧 Correo con IA** — Redacta un correo relacionado al registro

### Ejemplos de prompts

**Crear factura:**
```
Crea una factura para el cliente "Empresa ABC" 
con 3 líneas:
- 5 horas de consultoría a $120/hora
- Licencia software mensual $200
- Soporte técnico $150
Fecha: hoy
```

**Redactar correo:**
```
Escribe un correo de seguimiento al cliente Juan García
sobre la propuesta enviada la semana pasada,
mencionando que tenemos una promoción del 10% válida 
hasta fin de mes.
```

**Traducir:**
```
This module integrates multiple AI providers with Odoo 16
to automate business operations through natural language.
```

---

## 🔧 Personalizar Plantillas de Prompt

Ve a **Configuración > Plantillas de Prompt** para crear prompts especializados.

Variables disponibles en las plantillas:
- `{input}` — Lo que escribe el usuario
- `{company}` — Nombre de la empresa
- `{user}` — Usuario actual
- `{date}` — Fecha actual
- `{record_name}` — Nombre del registro activo
- `{record}` — Datos del registro activo (JSON)

---

## 📊 Monitoreo

**🤖 IA Assistant > Reportes > Log de Solicitudes**

Muestra todas las llamadas realizadas con:
- Proveedor utilizado
- Tokens consumidos
- Prompt enviado y respuesta
- Usuario y fecha

---

## 🔒 Permisos

| Grupo | Permisos |
|-------|----------|
| Usuarios (todos) | Usar el asistente, ver sus propios logs |
| Administradores | Configurar proveedores, ver todos los logs |

---

## 🛠️ Agregar el botón IA a tus propias vistas

```xml
<!-- En cualquier vista form que herede mail.thread -->
<xpath expr="//header" position="inside">
    <button name="action_open_ai_assistant" string="🤖 IA" 
            type="object" class="btn-secondary"/>
</xpath>
```

---

## 📝 Notas Técnicas

- Compatible con **Odoo 16 Community y Enterprise**
- Requiere Python `requests` (incluido por defecto en la mayoría de instalaciones)
- Las API keys se almacenan encriptadas en la base de datos
- El módulo maneja timeouts y errores de conexión gracefully
