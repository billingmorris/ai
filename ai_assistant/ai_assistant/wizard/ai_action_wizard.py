# -*- coding: utf-8 -*-
import json
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AiActionWizard(models.TransientModel):
    _name = 'ai.action.wizard'
    _description = 'Asistente de Acciones IA'

    # Contexto del registro origen
    res_model = fields.Char(string='Modelo Origen')
    res_id = fields.Integer(string='ID Origen')
    record_name = fields.Char(string='Registro')

    # Configuración
    provider_id = fields.Many2one('ai.provider', string='Proveedor',
        default=lambda self: self._get_default_provider())
    template_id = fields.Many2one('ai.prompt.template', string='Plantilla')
    action_type = fields.Selection([
        ('create_invoice', '🧾 Crear Factura'),
        ('create_sale_order', '🛒 Crear Pedido de Venta'),
        ('send_email', '📧 Redactar Correo'),
        ('draft_text', '✏️ Redactar Texto'),
        ('analyze', '📊 Analizar Datos'),
        ('summarize', '📋 Resumir'),
        ('translate', '🌐 Traducir'),
        ('custom', '⚙️ Instrucción Personalizada'),
    ], string='Acción', default='draft_text', required=True)

    # Entrada del usuario
    user_input = fields.Text(string='Instrucción / Descripción', required=True,
        placeholder='Ej: Crea una factura para el cliente Acme Corp por 500 USD de consultoría...')
    target_language = fields.Selection([
        ('es', 'Español'), ('en', 'Inglés'), ('fr', 'Francés'),
        ('pt', 'Portugués'), ('de', 'Alemán'),
    ], string='Idioma Destino', default='es')

    # Respuesta
    ai_response = fields.Text(string='Respuesta de la IA', readonly=True)
    response_html = fields.Html(string='Vista Previa', readonly=True)
    is_loading = fields.Boolean(default=False)
    error_message = fields.Char(string='Error', readonly=True)

    # Resultado de la acción
    result_record_id = fields.Integer(string='ID Registro Creado', readonly=True)
    result_model = fields.Char(string='Modelo Creado', readonly=True)

    def _get_default_provider(self):
        try:
            return self.env['ai.provider'].get_default_provider()
        except Exception:
            return False

    @api.onchange('action_type')
    def _onchange_action_type(self):
        if self.action_type:
            template = self.env['ai.prompt.template'].search([
                ('action_type', '=', self.action_type), ('active', '=', True)
            ], limit=1)
            self.template_id = template

    def _get_system_prompt(self):
        """Construye el system prompt según la acción."""
        base = """Eres un asistente empresarial experto integrado en Odoo 16.
Responde siempre en español. Sé preciso y estructurado."""

        if self.template_id and self.template_id.system_prompt:
            return self.template_id.system_prompt

        action_prompts = {
            'create_invoice': base + """
Cuando se te pida crear una factura, responde ÚNICAMENTE con un JSON válido con esta estructura:
{
  "partner_name": "Nombre del cliente",
  "invoice_date": "YYYY-MM-DD",
  "lines": [
    {"name": "Descripción del producto/servicio", "quantity": 1, "price_unit": 100.0}
  ],
  "notes": "Notas adicionales"
}""",
            'create_sale_order': base + """
Cuando se te pida crear un pedido de venta, responde ÚNICAMENTE con un JSON:
{
  "partner_name": "Nombre del cliente",
  "order_date": "YYYY-MM-DD",
  "lines": [
    {"product_name": "Nombre producto", "quantity": 1, "price_unit": 100.0}
  ],
  "notes": "Notas"
}""",
            'send_email': base + """
Redacta correos profesionales, formales y claros.
Estructura: Asunto en la primera línea con prefijo "Asunto:", luego el cuerpo del correo.""",
            'draft_text': base + " Redacta textos claros, profesionales y bien estructurados.",
            'analyze': base + " Analiza los datos proporcionados y presenta conclusiones claras con puntos clave.",
            'summarize': base + " Genera resúmenes concisos manteniendo los puntos más importantes.",
            'translate': base + f" Traduce el texto al {self.target_language or 'español'} manteniendo el tono y formato original.",
            'custom': base,
        }
        return action_prompts.get(self.action_type, base)

    def action_call_ai(self):
        """Llama a la IA y guarda la respuesta."""
        self.ensure_one()
        if not self.provider_id:
            raise UserError(_('Selecciona un proveedor de IA.'))
        if not self.user_input:
            raise UserError(_('Escribe una instrucción para la IA.'))

        # Construir prompt final
        prompt = self.user_input
        if self.template_id:
            record = None
            if self.res_model and self.res_id:
                try:
                    record = self.env[self.res_model].browse(self.res_id)
                except Exception:
                    pass
            prompt = self.template_id.render_prompt(self.user_input, record)

        system_prompt = self._get_system_prompt()

        try:
            response = self.provider_id.call_ai(prompt, system_prompt=system_prompt)
            self.ai_response = response
            # Formatear HTML para preview
            html = response.replace('\n', '<br/>')
            self.response_html = f'<div style="font-family:monospace;padding:10px;background:#f8f9fa;border-radius:4px">{html}</div>'
            self.error_message = False
        except UserError as e:
            self.error_message = str(e)
            self.ai_response = False

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }

    def action_apply_response(self):
        """Aplica la respuesta según el tipo de acción."""
        self.ensure_one()
        if not self.ai_response:
            raise UserError(_('Primero genera una respuesta con la IA.'))

        if self.action_type == 'create_invoice':
            return self._create_invoice_from_response()
        elif self.action_type == 'create_sale_order':
            return self._create_sale_order_from_response()
        elif self.action_type == 'send_email':
            return self._compose_email_from_response()
        else:
            # Para texto: copiar al portapapeles o mostrar
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('✅ Texto listo'),
                    'message': _('El texto generado está disponible en el campo de respuesta.'),
                    'type': 'success',
                }
            }

    def _parse_json_response(self):
        """Intenta extraer JSON de la respuesta."""
        text = self.ai_response
        # Limpiar markdown code blocks
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0]
        elif '```' in text:
            text = text.split('```')[1].split('```')[0]
        return json.loads(text.strip())

    def _create_invoice_from_response(self):
        """Crea una factura de cliente a partir de la respuesta JSON."""
        try:
            data = self._parse_json_response()
        except (json.JSONDecodeError, IndexError) as e:
            raise UserError(_('La IA no devolvió un JSON válido para crear la factura. '
                              'Intenta de nuevo con una descripción más específica.'))

        # Buscar/crear cliente
        partner = self.env['res.partner'].search([
            ('name', 'ilike', data.get('partner_name', ''))
        ], limit=1)
        if not partner:
            raise UserError(_('No se encontró el cliente: %s') % data.get('partner_name'))

        # Preparar líneas de factura
        invoice_lines = []
        for line in data.get('lines', []):
            account = self.env['account.account'].search([
                ('account_type', 'in', ['income', 'income_other']),
                ('company_id', '=', self.env.company.id),
            ], limit=1)
            invoice_lines.append((0, 0, {
                'name': line.get('name', 'Servicio'),
                'quantity': float(line.get('quantity', 1)),
                'price_unit': float(line.get('price_unit', 0)),
                'account_id': account.id,
            }))

        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': partner.id,
            'invoice_date': data.get('invoice_date') or fields.Date.today(),
            'invoice_line_ids': invoice_lines,
            'narration': data.get('notes', f'Creado con IA - {self.env.user.name}'),
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': invoice.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _create_sale_order_from_response(self):
        """Crea un pedido de venta a partir de la respuesta JSON."""
        try:
            data = self._parse_json_response()
        except (json.JSONDecodeError, IndexError):
            raise UserError(_('La IA no devolvió un JSON válido. Intenta de nuevo.'))

        partner = self.env['res.partner'].search([
            ('name', 'ilike', data.get('partner_name', ''))
        ], limit=1)
        if not partner:
            raise UserError(_('No se encontró el cliente: %s') % data.get('partner_name'))

        order_lines = []
        for line in data.get('lines', []):
            product = self.env['product.product'].search([
                ('name', 'ilike', line.get('product_name', ''))
            ], limit=1)
            order_lines.append((0, 0, {
                'product_id': product.id if product else False,
                'name': line.get('product_name', 'Producto'),
                'product_uom_qty': float(line.get('quantity', 1)),
                'price_unit': float(line.get('price_unit', 0)),
            }))

        order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'date_order': data.get('order_date') or fields.Datetime.now(),
            'order_line': order_lines,
            'note': data.get('notes', f'Creado con IA - {self.env.user.name}'),
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'res_id': order.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _compose_email_from_response(self):
        """Abre el compositor de correo con el texto generado."""
        lines = self.ai_response.split('\n')
        subject = ''
        body_lines = []

        for line in lines:
            if line.startswith('Asunto:'):
                subject = line.replace('Asunto:', '').strip()
            else:
                body_lines.append(line)

        body = '<br/>'.join(body_lines)

        ctx = {
            'default_subject': subject,
            'default_body': body,
        }
        if self.res_model and self.res_id:
            ctx['default_model'] = self.res_model
            ctx['default_res_id'] = self.res_id
            ctx['default_res_ids'] = [self.res_id]

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mail.compose.message',
            'view_mode': 'form',
            'target': 'new',
            'context': ctx,
        }
