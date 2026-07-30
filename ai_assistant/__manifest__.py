# -*- coding: utf-8 -*-
{
    'name': 'AI Assistant Integration',
    'version': '16.0.1.0.0',
    'summary': 'Integración de IA (ChatGPT, DeepSeek, Claude) para automatizar operaciones en Odoo',
    'description': """
        Módulo que integra múltiples proveedores de IA para automatizar:
        - Creación de documentos (facturas, pedidos, contratos)
        - Envío de correos inteligentes
        - Redacción de textos y respuestas
        - Análisis de datos y reportes
        - Asistente virtual en cualquier vista
    """,
    'category': 'Productivity',
    'author': 'Tu Empresa',
    'website': 'https://tu-empresa.com',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
        'account',
        'sale',
        'purchase',
        'web',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/ai_provider_views.xml',
        'views/ai_prompt_template_views.xml',
        'views/ai_log_views.xml',
        'wizard/ai_action_wizard_views.xml',
        'views/menu_views.xml',
        'views/res_config_settings_views.xml',
        'data/ai_provider_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ai_assistant/static/src/js/ai_assistant_widget.js',
            'ai_assistant/static/src/css/ai_assistant.css',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
    'images': ['static/src/img/banner.png'],
}
