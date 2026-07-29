# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class AiPromptTemplate(models.Model):
    _name = 'ai.prompt.template'
    _description = 'Plantilla de Prompt para IA'
    _order = 'category, name'

    name = fields.Char(string='Nombre', required=True)
    category = fields.Selection([
        ('document', 'Documentos'),
        ('email', 'Correos'),
        ('analysis', 'Análisis'),
        ('sales', 'Ventas'),
        ('hr', 'Recursos Humanos'),
        ('custom', 'Personalizado'),
    ], string='Categoría', default='custom', required=True)

    model_id = fields.Many2one('ir.model', string='Modelo Odoo',
        help='Modelo de Odoo al que aplica esta plantilla')
    action_type = fields.Selection([
        ('create_invoice', 'Crear Factura'),
        ('create_sale_order', 'Crear Pedido de Venta'),
        ('send_email', 'Enviar Correo'),
        ('draft_text', 'Redactar Texto'),
        ('analyze', 'Analizar Datos'),
        ('summarize', 'Resumir'),
        ('translate', 'Traducir'),
        ('custom', 'Acción Personalizada'),
    ], string='Tipo de Acción', default='draft_text')

    system_prompt = fields.Text(
        string='Prompt del Sistema',
        help='Instrucciones base que definen el comportamiento del modelo',
        default="""Eres un asistente experto en gestión empresarial integrado en Odoo.
Responde siempre en español, de forma clara y estructurada.
Cuando generes datos para documentos, usa formato JSON válido."""
    )
    user_prompt_template = fields.Text(
        string='Plantilla del Prompt',
        help='Usa {variable} para insertar datos dinámicos del registro',
        default='Ayúdame con: {input}'
    )
    active = fields.Boolean(default=True)
    provider_id = fields.Many2one('ai.provider', string='Proveedor (opcional)',
        help='Si no se especifica, se usará el proveedor por defecto')

    # Variables disponibles mostradas al usuario
    available_variables = fields.Html(
        string='Variables Disponibles',
        compute='_compute_available_variables'
    )

    @api.depends('model_id')
    def _compute_available_variables(self):
        for rec in self:
            vars_html = '<ul>'
            vars_html += '<li><code>{input}</code> — Texto ingresado por el usuario</li>'
            vars_html += '<li><code>{record_name}</code> — Nombre del registro actual</li>'
            vars_html += '<li><code>{company}</code> — Nombre de la empresa</li>'
            vars_html += '<li><code>{user}</code> — Usuario actual</li>'
            vars_html += '<li><code>{date}</code> — Fecha actual</li>'
            if rec.model_id:
                vars_html += f'<li><code>{{record}}</code> — Datos del registro ({rec.model_id.name})</li>'
            vars_html += '</ul>'
            rec.available_variables = vars_html

    def render_prompt(self, user_input, record=None):
        """Renderiza el prompt con los datos del contexto."""
        self.ensure_one()
        ctx = {
            'input': user_input,
            'company': self.env.company.name,
            'user': self.env.user.name,
            'date': fields.Date.today().strftime('%d/%m/%Y'),
            'record_name': '',
            'record': '',
        }
        if record:
            ctx['record_name'] = getattr(record, 'name', str(record.id))
            # Serializar campos básicos del registro
            record_data = {}
            for fname in ['name', 'partner_id', 'amount_total', 'state', 'date']:
                if hasattr(record, fname):
                    val = getattr(record, fname)
                    if hasattr(val, 'name'):
                        val = val.name
                    record_data[fname] = str(val) if val else ''
            ctx['record'] = str(record_data)

        try:
            return self.user_prompt_template.format(**ctx)
        except KeyError as e:
            return self.user_prompt_template  # devolver sin renderizar si falla
