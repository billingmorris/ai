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
        ('create_sale_order', '🛒 Crear Cotización/Pedido'),
        ('create_invoice',    '🧾 Crear Factura'),
        ('send_email',        '📧 Redactar Correo'),
        ('draft_text',        '✏️ Redactar Texto'),
        ('analyze',           '📊 Analizar Datos'),
        ('summarize',         '📋 Resumir'),
        ('translate',         '🌐 Traducir'),
        ('custom',            '⚙️ Instrucción Personalizada'),
    ], string='Acción', default='create_sale_order', required=True)

    # Entrada del usuario
    user_input = fields.Text(
        string='Instrucción',
        required=True,
        placeholder='Ej: Crea una cotización para Sumilab con 20 kg de Glicerina USP...',
    )
    target_language = fields.Selection([
        ('es', 'Español'), ('en', 'Inglés'), ('fr', 'Francés'),
        ('pt', 'Portugués'), ('de', 'Alemán'),
    ], string='Idioma Destino', default='es')

    # Respuesta IA
    ai_response  = fields.Text(string='JSON generado por la IA', readonly=True)
    response_html = fields.Html(string='Vista Previa', readonly=True)
    error_message = fields.Char(string='Error', readonly=True)

    # ------------------------------------------------------------------ #
    #  HELPERS                                                             #
    # ------------------------------------------------------------------ #

    def _get_default_provider(self):
        try:
            return self.env['ai.provider'].get_default_provider()
        except Exception:
            return False

    @api.onchange('action_type')
    def _onchange_action_type(self):
        if self.action_type:
            tmpl = self.env['ai.prompt.template'].search(
                [('action_type', '=', self.action_type), ('active', '=', True)], limit=1)
            self.template_id = tmpl

    # ---------- catálogo disponible para el prompt ------------------- #

    def _get_catalog_context(self):
        """
        Devuelve listas reales de clientes y productos de Odoo
        para incluirlas en el prompt y que la IA use nombres exactos.
        """
        # Clientes (solo los primeros 80 para no saturar el prompt)
        partners = self.env['res.partner'].search(
            [('customer_rank', '>', 0), ('active', '=', True)], limit=80)
        partner_list = ', '.join(p.name for p in partners) or 'ninguno'

        # Productos vendibles
        products = self.env['product.product'].search(
            [('sale_ok', '=', True), ('active', '=', True)], limit=100)
        product_lines = []
        for p in products:
            uom = p.uom_id.name if p.uom_id else ''
            price = p.lst_price or 0.0
            product_lines.append(f'  - "{p.name}" | UOM: {uom} | precio: {price}')
        product_list = '\n'.join(product_lines) or '  - (sin productos)'

        return partner_list, product_list

    # ---------- system prompts --------------------------------------- #

    def _get_system_prompt(self):
        if self.template_id and self.template_id.system_prompt:
            return self.template_id.system_prompt

        base = (
            "Eres un asistente empresarial integrado en Odoo 16. "
            "Responde SIEMPRE en español."
        )

        partner_list, product_list = self._get_catalog_context()

        if self.action_type == 'create_sale_order':
            return f"""{base}

Tu tarea es extraer los datos de una cotización/pedido de venta y devolverlos en JSON puro,
sin texto adicional, sin markdown, sin explicaciones.

CLIENTES DISPONIBLES EN ODOO (usa el nombre exacto):
{partner_list}

PRODUCTOS DISPONIBLES EN ODOO (usa el nombre exacto):
{product_list}

Devuelve exactamente este esquema JSON:
{{
  "partner_name": "<nombre exacto del cliente de la lista>",
  "validity_date": "<YYYY-MM-DD o null>",
  "notes": "<observaciones generales o null>",
  "lines": [
    {{
      "product_name": "<nombre exacto del producto de la lista>",
      "quantity": <número>,
      "price_unit": <número o null para usar precio de lista>,
      "uom_name": "<unidad de medida o null>"
    }}
  ]
}}

Si el cliente o producto no están en las listas, usa el nombre tal como lo mencionó el usuario."""

        if self.action_type == 'create_invoice':
            return f"""{base}

Tu tarea es extraer los datos de una factura y devolverlos en JSON puro, sin texto adicional.

CLIENTES DISPONIBLES:
{partner_list}

PRODUCTOS DISPONIBLES:
{product_list}

Esquema JSON:
{{
  "partner_name": "<nombre exacto>",
  "invoice_date": "<YYYY-MM-DD o null>",
  "notes": "<observaciones o null>",
  "lines": [
    {{
      "product_name": "<nombre exacto o descripción libre>",
      "quantity": <número>,
      "price_unit": <número>
    }}
  ]
}}"""

        if self.action_type == 'send_email':
            return (base +
                "\n\nRedacta un correo profesional. "
                "Primera línea: 'Asunto: <asunto>'. Luego el cuerpo.")

        prompts = {
            'draft_text': base + " Redacta el texto solicitado de forma clara y profesional.",
            'analyze':    base + " Analiza la información y presenta conclusiones con puntos clave.",
            'summarize':  base + " Resume manteniendo los puntos más importantes.",
            'translate':  base + f" Traduce al {self.target_language or 'español'} sin añadir comentarios.",
            'custom':     base,
        }
        return prompts.get(self.action_type, base)

    # ------------------------------------------------------------------ #
    #  ACCIÓN PRINCIPAL: llamar a la IA                                   #
    # ------------------------------------------------------------------ #

    def action_call_ai(self):
        self.ensure_one()
        if not self.provider_id:
            raise UserError(_('Selecciona un proveedor de IA.'))

        prompt = self.user_input
        if self.template_id:
            record = None
            if self.res_model and self.res_id:
                try:
                    record = self.env[self.res_model].browse(self.res_id)
                except Exception:
                    pass
            prompt = self.template_id.render_prompt(self.user_input, record)

        try:
            response = self.provider_id.call_ai(
                prompt, system_prompt=self._get_system_prompt())
            self.ai_response = response
            self.error_message = False

            # Preview HTML
            html = response.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br/>')
            self.response_html = (
                '<div style="font-family:monospace;padding:12px;'
                'background:#f4f6f9;border-radius:6px;white-space:pre-wrap">'
                + html + '</div>'
            )
        except UserError as e:
            self.error_message = str(e)
            self.ai_response = False

        return self._reopen_wizard()

    def _reopen_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }

    # ------------------------------------------------------------------ #
    #  APLICAR RESPUESTA                                                   #
    # ------------------------------------------------------------------ #

    def action_apply_response(self):
        self.ensure_one()
        if not self.ai_response:
            raise UserError(_('Primero genera una respuesta con la IA.'))

        dispatch = {
            'create_sale_order': self._create_sale_order,
            'create_invoice':    self._create_invoice,
            'send_email':        self._compose_email,
        }
        handler = dispatch.get(self.action_type)
        if handler:
            return handler()

        # Para acciones de texto: notificación
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('✅ Texto generado'),
                'message': _('Copia el texto del campo de respuesta.'),
                'type': 'success',
            }
        }

    # ------------------------------------------------------------------ #
    #  PARSEO DEL JSON                                                     #
    # ------------------------------------------------------------------ #

    def _parse_json(self):
        """Extrae el JSON de la respuesta aunque venga con markdown."""
        text = (self.ai_response or '').strip()
        # Quitar bloques de código markdown
        if '```json' in text:
            text = text.split('```json', 1)[1].split('```', 1)[0]
        elif '```' in text:
            text = text.split('```', 1)[1].split('```', 1)[0]
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError as e:
            raise UserError(
                _('La IA no devolvió un JSON válido.\n\nDetalle: %s\n\n'
                  'Respuesta recibida:\n%s') % (str(e), self.ai_response)
            )

    # ------------------------------------------------------------------ #
    #  BÚSQUEDAS EN ODOO                                                   #
    # ------------------------------------------------------------------ #

    def _find_partner(self, name):
        """Busca un cliente por nombre exacto primero, luego aproximado."""
        if not name:
            raise UserError(_('La IA no especificó un cliente.'))

        # 1. Coincidencia exacta (case-insensitive)
        partner = self.env['res.partner'].search(
            [('name', '=ilike', name), ('active', '=', True)], limit=1)
        if partner:
            return partner

        # 2. Coincidencia parcial
        partner = self.env['res.partner'].search(
            [('name', 'ilike', name), ('active', '=', True)], limit=1)
        if partner:
            return partner

        raise UserError(
            _('No se encontró el cliente "%s" en Odoo.\n'
              'Verifique que exista en Contactos.') % name
        )

    def _find_product(self, name):
        """Busca un producto por nombre exacto primero, luego aproximado."""
        if not name:
            return None

        product = self.env['product.product'].search(
            [('name', '=ilike', name), ('active', '=', True)], limit=1)
        if product:
            return product

        product = self.env['product.product'].search(
            [('name', 'ilike', name), ('active', '=', True)], limit=1)
        return product  # puede ser None, se manejará en el llamador

    def _find_uom(self, uom_name):
        """Busca una unidad de medida por nombre."""
        if not uom_name:
            return None
        uom = self.env['uom.uom'].search(
            [('name', '=ilike', uom_name)], limit=1)
        if not uom:
            uom = self.env['uom.uom'].search(
                [('name', 'ilike', uom_name)], limit=1)
        return uom or None

    # ------------------------------------------------------------------ #
    #  CREAR COTIZACIÓN / PEDIDO DE VENTA                                  #
    # ------------------------------------------------------------------ #

    def _build_sale_line_vals(self, order, line_data, index):
        """
        Construye los valores de una línea de pedido de venta de forma
        segura, garantizando que todos los campos obligatorios estén presentes.
        Devuelve (vals_dict, warning_str_or_None).
        """
        product_name = (line_data.get('product_name') or '').strip()
        product = self._find_product(product_name) if product_name else None
        warning = None

        qty   = float(line_data.get('quantity') or 1)
        price = line_data.get('price_unit')

        # --- UOM --------------------------------------------------------
        # Odoo 16 exige product_uom cuando hay product_id.
        # Si no hay producto usamos la UOM genérica "Units".
        uom = None
        if line_data.get('uom_name'):
            uom = self._find_uom(line_data['uom_name'])
        if not uom and product:
            uom = product.uom_id
        if not uom:
            uom = self.env.ref('uom.product_uom_unit', raise_if_not_found=False)
            if not uom:
                uom = self.env['uom.uom'].search([], limit=1)

        # --- Descripción ------------------------------------------------
        # 'name' es obligatorio; si hay producto usamos su nombre.
        name = (product.display_name if product else product_name) or _('Producto/Servicio')

        # --- Precio -----------------------------------------------------
        if price is not None:
            price_unit = float(price)
        elif product:
            # precio de lista del producto para el cliente del pedido
            price_unit = product.with_context(
                pricelist=order.pricelist_id.id if order.pricelist_id else False
            ).lst_price
        else:
            price_unit = 0.0

        # --- Impuestos --------------------------------------------------
        tax_ids = []
        if product and product.taxes_id:
            taxes = product.taxes_id.filtered(
                lambda t: t.company_id == self.env.company)
            tax_ids = [(6, 0, taxes.ids)]

        vals = {
            'order_id':        order.id,   # requerido en Odoo 16
            'product_id':      product.id if product else False,
            'name':            name,
            'product_uom_qty': qty,
            'product_uom':     uom.id,     # siempre presente
            'price_unit':      price_unit,
        }
        if tax_ids:
            vals['tax_id'] = tax_ids

        if not product:
            warning = _(
                'Línea %d: producto "%s" no encontrado en Odoo, '
                'se agregó como descripción libre.'
            ) % (index, product_name)

        return vals, warning

    def _create_sale_order(self):
        data = self._parse_json()

        partner = self._find_partner(data.get('partner_name', ''))

        # Crear el pedido SIN líneas primero para obtener order.id y pricelist
        order_vals = {
            'partner_id': partner.id,
        }
        if data.get('validity_date'):
            order_vals['validity_date'] = data['validity_date']
        if data.get('notes'):
            order_vals['note'] = data['notes']

        order = self.env['sale.order'].create(order_vals)

        # Ahora construir y agregar las líneas con order.id disponible
        warnings = []
        lines_data = data.get('lines', [])
        if not lines_data:
            order.unlink()
            raise UserError(_('La IA no generó ninguna línea de producto.'))

        for i, line_data in enumerate(lines_data, 1):
            vals, warning = self._build_sale_line_vals(order, line_data, i)
            if warning:
                warnings.append(warning)
            self.env['sale.order.line'].create(vals)

        if warnings:
            order.message_post(body='<br/>'.join(warnings))

        return {
            'type': 'ir.actions.act_window',
            'name': _('Cotización creada'),
            'res_model': 'sale.order',
            'res_id': order.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ------------------------------------------------------------------ #
    #  CREAR FACTURA                                                        #
    # ------------------------------------------------------------------ #

    def _create_invoice(self):
        data = self._parse_json()

        partner = self._find_partner(data.get('partner_name', ''))

        # Cuenta de ingresos por defecto
        account = self.env['account.account'].search([
            ('account_type', 'in', ['income', 'income_other']),
            ('company_id', '=', self.env.company.id),
            ('deprecated', '=', False),
        ], limit=1)

        invoice_lines = []
        warnings = []

        for i, line in enumerate(data.get('lines', []), 1):
            product_name = line.get('product_name', '')
            product = self._find_product(product_name)

            if not product:
                warnings.append(
                    _('Línea %d: producto "%s" no encontrado, se usó descripción libre.') % (i, product_name)
                )

            qty   = float(line.get('quantity') or 1)
            price = float(line.get('price_unit') or 0)

            line_vals = {
                'name':       product.display_name if product else product_name,
                'quantity':   qty,
                'price_unit': price,
                'account_id': account.id if account else False,
            }
            if product:
                line_vals['product_id'] = product.id
                # Usar cuenta del producto si tiene
                if product.property_account_income_id:
                    line_vals['account_id'] = product.property_account_income_id.id
                elif product.categ_id.property_account_income_categ_id:
                    line_vals['account_id'] = product.categ_id.property_account_income_categ_id.id

            invoice_lines.append((0, 0, line_vals))

        if not invoice_lines:
            raise UserError(_('La IA no generó ninguna línea para la factura.'))

        invoice_vals = {
            'move_type':         'out_invoice',
            'partner_id':        partner.id,
            'invoice_line_ids':  invoice_lines,
        }
        if data.get('invoice_date'):
            invoice_vals['invoice_date'] = data['invoice_date']
        if data.get('notes'):
            invoice_vals['narration'] = data['notes']

        invoice = self.env['account.move'].create(invoice_vals)

        if warnings:
            invoice.message_post(body='\n'.join(warnings))

        return {
            'type': 'ir.actions.act_window',
            'name': _('Factura creada'),
            'res_model': 'account.move',
            'res_id': invoice.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ------------------------------------------------------------------ #
    #  REDACTAR CORREO                                                      #
    # ------------------------------------------------------------------ #

    def _compose_email(self):
        lines = (self.ai_response or '').split('\n')
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
            'default_body':    body,
        }
        if self.res_model and self.res_id:
            ctx['default_model']   = self.res_model
            ctx['default_res_id']  = self.res_id
            ctx['default_res_ids'] = [self.res_id]

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mail.compose.message',
            'view_mode': 'form',
            'target': 'new',
            'context': ctx,
        }
