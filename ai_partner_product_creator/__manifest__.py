# -*- coding: utf-8 -*-
{
    'name': 'AI Creator — Clientes, Proveedores y Productos',
    'version': '16.0.1.0.0',
    'summary': 'Crea clientes, proveedores y productos en Odoo escribiendo en lenguaje natural',
    'description': """
Permite al usuario escribir instrucciones como:
  • "Crear cliente Comercializadora ABC SAS, NIT 901234567-8, Medellín..."
  • "Crear proveedor Industrias López, correo compras@lopez.com..."
  • "Crear producto Shampoo Romero 500ml, precio 28000, costo 17000..."

El módulo llama a la IA configurada (Claude, ChatGPT o DeepSeek),
extrae los campos en JSON y crea el registro directamente en Odoo.
    """,
    'category': 'Productivity',
    'author': 'Tu Empresa',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'stock', 'purchase'],
    'data': [
        'security/ir.model.access.csv',
        'views/ai_creator_wizard_views.xml',
        'views/ai_creator_log_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
