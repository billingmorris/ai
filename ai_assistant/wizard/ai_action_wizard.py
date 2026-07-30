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
    action_type = fields.Selection([
        ('create_sale_order', 'Crear Cotización/Pedido'),
        ('create_invoice',    'Crear Factura'),
        ('send_email',        'Redactar Correo'),
        ('draft_text',        'Redactar Texto'),
        ('summarize',         'Resumir'),
        ('translate',         'Traducir'),
    ], string='Tipo de Acción detectado', readonly=True)

    # Entrada del usuario
    user_input = fields.Text(
        string='Instrucción',
        required=True,
        placeholder='Ej: Crea una cotización para Sumilab con 20 kg de Glicerina USP...',
    )

    # Respuesta IA
    ai_response  = fields.Text(string='JSON generado por la IA', readonly=True)
    response_html = fields.Html(string='Vista Previa', readonly=True)
    error_message = fields.Char(string='Error', readonly=True)

    # Adjuntos
    attachment_ids = fields.Many2many(
        'ir.attachment',
        'ai_wizard_attachment_rel',
        'wizard_id', 'attachment_id',
        string='Archivos adjuntos',
        help='Adjunta imágenes (PNG, JPG) o PDFs para que la IA los analice',
    )
    has_attachment = fields.Boolean(compute='_compute_has_attachment')

    @api.depends('attachment_ids')
    def _compute_has_attachment(self):
        for rec in self:
            rec.has_attachment = bool(rec.attachment_ids)

    # ------------------------------------------------------------------ #
    #  HELPERS                                                             #
    # ------------------------------------------------------------------ #

    def _get_default_provider(self):
        try:
            return self.env['ai.provider'].get_default_provider()
        except Exception:
            return False

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
        partner_list, product_list = self._get_catalog_context()
        return (
            "Eres un asistente empresarial integrado en Odoo 16. Responde SIEMPRE en español.\n\n"
            "Tu trabajo es interpretar la instrucción del usuario, determinar qué acción ejecutar "
            "y devolver un JSON puro sin texto adicional, sin markdown, sin explicaciones.\n\n"
            "El JSON siempre debe incluir el campo: \"intent\" con uno de estos valores:\n"
            "  create_sale_order | create_invoice | send_email | draft_text | summarize | translate\n\n"
            "CLIENTES DISPONIBLES EN ODOO (usa el nombre exacto):\n"
            + partner_list + "\n\n"
            "PRODUCTOS DISPONIBLES EN ODOO (nombre | UOM | precio):\n"
            + product_list + "\n\n"
            "Esquemas por intent:\n\n"
            "intent=create_sale_order -> {intent, partner_name, validity_date, notes, "
            "lines:[{product_name, quantity, price_unit, uom_name}]}\n\n"
            "intent=create_invoice -> {intent, partner_name, invoice_date, notes, "
            "lines:[{product_name, quantity, price_unit}]}\n\n"
            "intent=send_email -> {intent, subject, body}\n\n"
            "intent=draft_text|summarize|translate -> {intent, text}\n\n"
            "Reglas:\n"
            "- Devuelve SOLO el JSON, nada mas.\n"
            "- Si cliente o producto no estan en la lista, usa el nombre como lo escribio el usuario.\n"
            "- Cantidades y precios son numeros, nunca strings."
        )

    # ------------------------------------------------------------------ #
    #  ACCIÓN PRINCIPAL: llamar a la IA                                   #
    # ------------------------------------------------------------------ #

    def action_call_ai(self):
        self.ensure_one()
        if not self.provider_id:
            raise UserError(_('Selecciona un proveedor de IA.'))

        prompt = self.user_input

        try:
            response = self.provider_id.call_ai(
                prompt,
                system_prompt=self._get_system_prompt(),
                attachments=self.attachment_ids,
            )
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

        data = self._parse_json()
        intent = data.get('intent', 'draft_text')

        dispatch = {
            'create_sale_order': self._create_sale_order,
            'create_invoice':    self._create_invoice,
            'send_email':        self._compose_email,
        }
        handler = dispatch.get(intent)
        if handler:
            return handler()

        # draft_text / summarize / translate
        text = data.get('text', self.ai_response)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Texto generado'),
                'message': text[:200] + ('...' if len(text) > 200 else ''),
                'type': 'success',
                'sticky': True,
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

    def _normalize(self, text):
        """Normaliza texto: minúsculas, sin tildes, sin caracteres especiales."""
        import unicodedata
        text = text.lower().strip()
        text = unicodedata.normalize('NFD', text)
        text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
        return text

    def _find_partner(self, name):
        """
        Busca un cliente con múltiples estrategias:
        1. Nombre exacto (=ilike)
        2. Nombre comercial / company_name
        3. Coincidencia parcial (ilike)
        4. Por palabras clave individuales
        5. Nombre normalizado sin tildes
        """
        if not name:
            raise UserError(_("La IA no especificó un cliente."))

        name = name.strip()
        Partner = self.env["res.partner"]
        base_domain = [("active", "=", True)]

        # 1. Exacto
        p = Partner.search(base_domain + [("name", "=ilike", name)], limit=1)
        if p:
            return p

        # 2. Nombre comercial (commercial_company_name) y ref interna
        p = Partner.search(base_domain + [
            "|", "|",
            ("commercial_company_name", "ilike", name),
            ("ref", "ilike", name),
            ("display_name", "ilike", name),
        ], limit=1)
        if p:
            return p

        # 3. Parcial en name
        p = Partner.search(base_domain + [("name", "ilike", name)], limit=1)
        if p:
            return p

        # 4. Palabras clave: buscar cada palabra significativa (>3 chars)
        words = [w for w in name.split() if len(w) > 3]
        for word in words:
            p = Partner.search(base_domain + [
                "|",
                ("name", "ilike", word),
                ("commercial_company_name", "ilike", word),
            ], limit=1)
            if p:
                _logger.info("Partner encontrado por palabra clave '%s': %s", word, p.name)
                return p

        # 5. Búsqueda con name_search (usa el método propio del modelo, incluye alias)
        results = Partner.name_search(name, limit=1)
        if results:
            return Partner.browse(results[0][0])

        raise UserError(
            _('No se encontró el cliente "%s" en Odoo.\n'
              'Verifique que exista en Contactos.') % name
        )


    def _find_product(self, name):
        """
        Busca un producto con múltiples estrategias:
        1. Exacto
        2. Parcial
        3. Por palabras clave significativas (todas deben estar presentes)
        4. Por cualquier palabra clave (al menos una)
        5. name_search del modelo
        """
        if not name:
            return None

        name = name.strip()
        Product = self.env["product.product"]
        base_domain = [("active", "=", True), ("sale_ok", "=", True)]

        # 1. Exacto
        p = Product.search(base_domain + [("name", "=ilike", name)], limit=1)
        if p:
            return p

        # 2. Parcial completo
        p = Product.search(base_domain + [("name", "ilike", name)], limit=1)
        if p:
            return p

        # 3. Todas las palabras clave deben estar en el nombre (AND)
        words = [w for w in name.split() if len(w) > 3]
        if words:
            domain = list(base_domain)
            for word in words:
                domain.append(("name", "ilike", word))
            p = Product.search(domain, limit=1)
            if p:
                _logger.info("Producto encontrado por palabras AND '%s': %s", name, p.name)
                return p

        # 4. Al menos una palabra clave coincide (OR) — tomar el que más palabras comparte
        if words:
            or_domain = [base_domain[0], base_domain[1]]
            or_clauses = []
            for word in words:
                or_clauses += [("name", "ilike", word)]
            if len(or_clauses) > 1:
                or_domain += ["|"] * (len(or_clauses) - 1) + or_clauses
            else:
                or_domain += or_clauses
            candidates = Product.search(or_domain, limit=20)
            if candidates:
                # Ordenar por cantidad de palabras coincidentes
                norm_name = self._normalize(name)
                best = max(candidates, key=lambda p: sum(
                    1 for w in words if w.lower() in self._normalize(p.name)
                ))
                _logger.info("Producto encontrado por palabras OR '%s': %s", name, best.name)
                return best

        # 5. name_search
        results = Product.name_search(name, limit=1)
        if results:
            return Product.browse(results[0][0])

        return None

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
