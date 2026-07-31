# -*- coding: utf-8 -*-
import json
import logging
import unicodedata
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AiWizardLine(models.TransientModel):
    _name = 'ai.wizard.line'
    _description = 'Línea de producto para el asistente IA'

    wizard_id   = fields.Many2one('ai.action.wizard', ondelete='cascade')
    product_id  = fields.Many2one('product.product', string='Producto', required=True)
    name        = fields.Char(string='Descripción')
    quantity    = fields.Float(string='Cantidad', default=1.0)
    price_unit  = fields.Float(string='Precio Unitario')
    uom_id      = fields.Many2one('uom.uom', string='UOM')

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.name      = self.product_id.display_name
            self.uom_id    = self.product_id.uom_id
            self.price_unit = self.product_id.lst_price


class AiActionWizard(models.TransientModel):
    _name = 'ai.action.wizard'
    _description = 'Asistente de Acciones IA'

    # ── Proveedor IA ──────────────────────────────────────────────────
    provider_id = fields.Many2one('ai.provider', string='Proveedor IA',
        default=lambda self: self._get_default_provider())

    # ── Acción seleccionable ──────────────────────────────────────────
    action_type = fields.Selection([
        ('create_out_invoice', '🧾 Factura de Cliente (Venta)'),
        ('create_in_invoice',  '🧾 Factura de Proveedor (Compra)'),
        ('create_out_refund',  '↩️ Nota Crédito de Venta'),
        ('create_in_refund',   '↩️ Nota Crédito de Compra'),
        ('create_sale_order',  '🛒 Cotización / Pedido de Venta'),
        ('query',              '🔍 Consulta a la Base de Datos'),
        ('send_email',         '📧 Redactar Correo'),
        ('draft_text',         '✏️ Redactar / Resumir / Traducir'),
    ], string='Acción', required=True, default='create_out_invoice')

    # ── Partner ───────────────────────────────────────────────────────
    partner_id  = fields.Many2one('res.partner', string='Cliente / Proveedor')

    # ── Fecha e instrucciones ─────────────────────────────────────────
    date        = fields.Date(string='Fecha', default=fields.Date.today)
    ref         = fields.Char(string='Referencia / Nº Factura Proveedor')
    user_input  = fields.Text(string='Instrucciones adicionales / Prompt',
        placeholder='Ej: Agrega una nota de descuento del 5%, '
                    'o bien: Consulta todas las facturas de este cliente del mes pasado...')

    # ── Líneas de productos ───────────────────────────────────────────
    line_ids    = fields.One2many('ai.wizard.line', 'wizard_id', string='Productos')

    # ── Adjuntos ──────────────────────────────────────────────────────
    attachment_ids = fields.Many2many(
        'ir.attachment', 'ai_wizard_attachment_rel',
        'wizard_id', 'attachment_id',
        string='Adjuntos (PDF / imágenes)',
    )

    # ── Respuesta ─────────────────────────────────────────────────────
    ai_response   = fields.Text(string='Respuesta IA', readonly=True)
    response_html = fields.Html(string='Vista Previa', readonly=True)
    error_message = fields.Char(string='Error', readonly=True)

    # Contexto de origen (cuando se abre desde un registro)
    res_model   = fields.Char(invisible=True)
    res_id      = fields.Integer(invisible=True)
    record_name = fields.Char(invisible=True)

    # ── Visibilidad helpers ───────────────────────────────────────────
    show_lines = fields.Boolean(compute='_compute_show_flags')
    show_partner = fields.Boolean(compute='_compute_show_flags')
    show_ref   = fields.Boolean(compute='_compute_show_flags')

    @api.depends('action_type')
    def _compute_show_flags(self):
        doc_types = {'create_out_invoice','create_in_invoice',
                     'create_out_refund','create_in_refund','create_sale_order'}
        for rec in self:
            rec.show_lines   = rec.action_type in doc_types
            rec.show_partner = rec.action_type in doc_types | {'send_email'}
            rec.show_ref     = rec.action_type in {'create_in_invoice','create_in_refund'}

    def _get_default_provider(self):
        try:
            return self.env['ai.provider'].get_default_provider()
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    #  NORMALIZACIÓN                                                       #
    # ------------------------------------------------------------------ #

    def _normalize(self, text):
        text = (text or '').lower().strip()
        text = unicodedata.normalize('NFD', text)
        return ''.join(c for c in text if unicodedata.category(c) != 'Mn')

    # ------------------------------------------------------------------ #
    #  CONSTRUCCIÓN DEL PROMPT PARA LA IA                                  #
    # ------------------------------------------------------------------ #

    def _build_prompt(self):
        """
        Construye el prompt completo a partir de los campos estructurados
        + instrucciones libres del usuario.
        Para acciones de documento: devuelve los datos listos para que la IA
        los complemente o genere el JSON final.
        Para consultas/texto: envía solo el user_input.
        """
        action_labels = dict(self._fields['action_type'].selection)
        label = action_labels.get(self.action_type, self.action_type)

        if self.action_type in ('query', 'send_email', 'draft_text'):
            return self.user_input or ''

        # Para documentos: armar prompt estructurado con los datos del form
        lines = []
        lines.append(f'Acción solicitada: {label}')

        if self.partner_id:
            lines.append(f'Cliente/Proveedor: {self.partner_id.name}')
        if self.date:
            lines.append(f'Fecha: {self.date}')
        if self.ref:
            lines.append(f'Referencia: {self.ref}')

        if self.line_ids:
            lines.append('\nProductos:')
            for l in self.line_ids:
                desc  = l.name or (l.product_id.display_name if l.product_id else '')
                uom   = l.uom_id.name if l.uom_id else ''
                price = f'${l.price_unit:,.2f}' if l.price_unit else 'precio de lista'
                lines.append(f'  - {desc} | Cant: {l.quantity} {uom} | Precio: {price}')

        if self.user_input:
            lines.append(f'\nInstrucciones adicionales: {self.user_input}')

        return '\n'.join(lines)

    def _get_system_prompt(self):
        action_map = {
            'create_out_invoice': 'out_invoice',
            'create_in_invoice':  'in_invoice',
            'create_out_refund':  'out_refund',
            'create_in_refund':   'in_refund',
            'create_sale_order':  'sale_order',
        }

        if self.action_type in action_map:
            move_type = action_map[self.action_type]
            return (
                "Eres un asistente de facturación en Odoo 16. Responde SOLO con JSON puro, "
                "sin markdown, sin texto adicional.\n\n"
                f"Debes generar un documento de tipo '{move_type}'.\n"
                "El usuario te enviará los datos estructurados. Tu tarea es:\n"
                "1. Validar y completar la información\n"
                "2. Aplicar instrucciones adicionales si las hay\n"
                "3. Devolver el JSON con este esquema exacto:\n\n"
                '{"intent":"' + move_type + '",'
                '"partner_name":"<nombre del cliente/proveedor>",'
                '"invoice_date":"<YYYY-MM-DD>",'
                '"ref":"<referencia o null>",'
                '"notes":"<notas o null>",'
                '"lines":['
                '{"product_name":"<nombre>","quantity":<n>,"price_unit":<n>,"uom_name":"<uom>"}'
                ']}\n\n'
                "Si el usuario no especificó precio, usa null para que Odoo tome el precio de lista."
            )

        if self.action_type == 'query':
            return (
                "Eres un analista de datos en Odoo 16. Responde SOLO con JSON puro.\n\n"
                "Esquema:\n"
                '{"intent":"query","query_type":"<invoices|sales|purchases|partners|products|stock>",'
                '"filters":{"partner_name":"<opt>","date_from":"<YYYY-MM-DD opt>",'
                '"date_to":"<YYYY-MM-DD opt>","state":"<opt>"},'
                '"question":"<pregunta original>"}'
            )

        if self.action_type == 'send_email':
            return (
                "Eres un redactor de correos profesionales en español. "
                "Responde SOLO con JSON puro.\n\n"
                'Esquema: {"intent":"send_email","subject":"<asunto>","body":"<cuerpo>"}'
            )

        # draft_text / translate / summarize
        return (
            "Eres un asistente de redacción en español. "
            "Responde SOLO con JSON puro.\n\n"
            'Esquema: {"intent":"draft_text","text":"<texto generado>"}'
        )

    # ------------------------------------------------------------------ #
    #  LLAMADA A LA IA                                                     #
    # ------------------------------------------------------------------ #

    def action_call_ai(self):
        self.ensure_one()
        if not self.provider_id:
            raise UserError(_('Selecciona un proveedor de IA.'))

        prompt = self._build_prompt()
        if not prompt and not self.attachment_ids:
            raise UserError(_('Escribe una instrucción o agrega productos.'))

        try:
            response = self.provider_id.call_ai(
                prompt or '(sin texto adicional)',
                system_prompt=self._get_system_prompt(),
                attachments=self.attachment_ids,
            )
            self.ai_response  = response
            self.error_message = False
            escaped = (response
                       .replace('&','&amp;').replace('<','&lt;').replace('>','&gt;'))
            self.response_html = (
                '<div style="font-family:monospace;padding:12px;background:#f4f6f9;'
                'border-radius:6px;white-space:pre-wrap;font-size:13px;">'
                + escaped + '</div>'
            )
        except UserError as e:
            self.error_message = str(e)
            self.ai_response   = False

        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }

    # ------------------------------------------------------------------ #
    #  APLICAR                                                             #
    # ------------------------------------------------------------------ #

    def action_apply_response(self):
        self.ensure_one()
        if not self.ai_response:
            raise UserError(_('Primero genera una respuesta con la IA.'))

        data   = self._parse_json()
        intent = data.get('intent', self.action_type.replace('create_', ''))

        dispatch = {
            'out_invoice':      lambda: self._create_move('out_invoice', data),
            'in_invoice':       lambda: self._create_move('in_invoice',  data),
            'out_refund':       lambda: self._create_move('out_refund',  data),
            'in_refund':        lambda: self._create_move('in_refund',   data),
            'sale_order':       lambda: self._create_sale_order(data),
            'create_out_invoice': lambda: self._create_move('out_invoice', data),
            'create_in_invoice':  lambda: self._create_move('in_invoice',  data),
            'create_out_refund':  lambda: self._create_move('out_refund',  data),
            'create_in_refund':   lambda: self._create_move('in_refund',   data),
            'create_sale_order':  lambda: self._create_sale_order(data),
            'query':            lambda: self._execute_query(data),
            'send_email':       lambda: self._compose_email(data),
        }
        handler = dispatch.get(intent)
        if handler:
            return handler()

        text = data.get('text', self.ai_response)
        self.response_html = (
            '<div style="padding:12px;background:#f4f6f9;border-radius:6px;white-space:pre-wrap;">'
            + text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
            + '</div>'
        )
        return self._reopen()

    # ------------------------------------------------------------------ #
    #  PARSEO JSON                                                         #
    # ------------------------------------------------------------------ #

    def _parse_json(self):
        text = (self.ai_response or '').strip()
        if '```json' in text:
            text = text.split('```json',1)[1].split('```',1)[0]
        elif '```' in text:
            text = text.split('```',1)[1].split('```',1)[0]
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError as e:
            raise UserError(
                _('La IA no devolvió un JSON válido.\nDetalle: %s\n\nRespuesta:\n%s')
                % (str(e), self.ai_response)
            )

    # ------------------------------------------------------------------ #
    #  BÚSQUEDA DE PARTNER — SQL DIRECTO 5 ESTRATEGIAS                     #
    # ------------------------------------------------------------------ #

    def _find_partner(self, name, supplier=False):
        # Si el usuario ya seleccionó el partner en el form, usarlo directamente
        if self.partner_id:
            return self.partner_id

        if not name:
            raise UserError(_('No se especificó cliente/proveedor.'))

        name = name.strip()
        cr   = self.env.cr

        def fetch(pid):
            return self.env['res.partner'].browse(pid)

        def log(label, pid, pname):
            _logger.info('[AI] Partner "%s" → %s (id=%s) por %s', name, pname, pid, label)

        # 1. Exacto
        cr.execute("""
            SELECT id, name FROM res_partner
            WHERE active=true
              AND (lower(name)=%s OR lower(COALESCE(commercial_company_name,''))=%s
                   OR lower(COALESCE(ref,''))=%s)
            ORDER BY (customer_rank+supplier_rank) DESC LIMIT 1
        """, [name.lower(), name.lower(), name.lower()])
        row = cr.fetchone()
        if row:
            log('exacto', row[0], row[1]); return fetch(row[0])

        # 2. ILIKE en todos los campos clave
        pct = f'%{name}%'
        cr.execute("""
            SELECT id, name FROM res_partner
            WHERE active=true AND (
                name ILIKE %s OR COALESCE(commercial_company_name,'') ILIKE %s
                OR COALESCE(display_name,'') ILIKE %s OR COALESCE(ref,'') ILIKE %s
                OR COALESCE(email,'') ILIKE %s OR COALESCE(vat,'') ILIKE %s
            )
            ORDER BY CASE WHEN lower(name)=%s THEN 0
                          WHEN name ILIKE %s THEN 1 ELSE 2 END,
                     (customer_rank+supplier_rank) DESC
            LIMIT 1
        """, [pct,pct,pct,pct,pct,pct, name.lower(), pct])
        row = cr.fetchone()
        if row:
            log('ilike', row[0], row[1]); return fetch(row[0])

        # 3. Tokens con scoring
        tokens = [t for t in name.split() if len(t) >= 2]
        if tokens:
            where = ' OR '.join(
                ["(name ILIKE %s OR COALESCE(commercial_company_name,'') ILIKE %s)"]
                * len(tokens)
            )
            params = []
            for t in tokens:
                params += [f'%{t}%', f'%{t}%']
            cr.execute(f"""
                SELECT id, name, COALESCE(commercial_company_name,name) AS cname
                FROM res_partner WHERE active=true AND ({where})
                ORDER BY (customer_rank+supplier_rank) DESC LIMIT 40
            """, params)
            rows = cr.fetchall()
            if rows:
                def score(r):
                    h = self._normalize(r[1]+' '+r[2])
                    return sum(1 for t in tokens if self._normalize(t) in h)
                best = max(rows, key=score)
                if score(best) > 0:
                    log(f'tokens ({score(best)}/{len(tokens)})', best[0], best[1])
                    return fetch(best[0])

        # 4. Normalizado sin tildes
        norm = self._normalize(name)
        cr.execute("""
            SELECT id, name, COALESCE(commercial_company_name,name) AS cname
            FROM res_partner WHERE active=true
            ORDER BY (customer_rank+supplier_rank) DESC LIMIT 1000
        """)
        best_id, best_name, best_sc = None, '', 0
        for pid, pname, cname in cr.fetchall():
            pn = self._normalize(pname or '')
            cn = self._normalize(cname or '')
            s  = 10 if (norm in pn or norm in cn) else \
                 8  if (pn in norm or cn in norm) else \
                 sum(1 for t in (tokens or [norm])
                     if self._normalize(t) in pn or self._normalize(t) in cn)
            if s > best_sc:
                best_sc, best_id, best_name = s, pid, pname
        if best_sc > 0 and best_id:
            log(f'normalizado(score={best_sc})', best_id, best_name)
            return fetch(best_id)

        # 5. name_search nativo
        res = self.env['res.partner'].name_search(name, limit=1)
        if res:
            p = self.env['res.partner'].browse(res[0][0])
            log('name_search', p.id, p.name); return p

        raise UserError(
            _('No se encontró el contacto "%s".\n'
              'Selecciónalo directamente en el campo Cliente/Proveedor.') % name
        )

    # ------------------------------------------------------------------ #
    #  BÚSQUEDA DE PRODUCTO                                                #
    # ------------------------------------------------------------------ #

    def _find_product(self, name):
        if not name:
            return None
        name    = name.strip()
        Product = self.env['product.product']
        domain  = [('active', '=', True)]

        p = Product.search(domain + [('name', '=ilike', name)], limit=1)
        if p: return p
        p = Product.search(domain + [('name', 'ilike', name)], limit=1)
        if p: return p

        tokens = [t for t in name.split() if len(t) >= 2]
        if tokens:
            d = list(domain)
            for t in tokens:
                d.append(('name', 'ilike', t))
            p = Product.search(d, limit=1)
            if p: return p

            # OR con scoring
            clauses = [('name', 'ilike', t) for t in tokens]
            or_d = list(domain)
            if len(clauses) > 1:
                or_d += ['|'] * (len(clauses) - 1)
            or_d += clauses
            candidates = Product.search(or_d, limit=30)
            if candidates:
                best = max(candidates, key=lambda prod: sum(
                    1 for t in tokens if t.lower() in self._normalize(prod.name)
                ))
                if sum(1 for t in tokens if t.lower() in self._normalize(best.name)) > 0:
                    return best

        res = Product.name_search(name, limit=1)
        if res:
            return Product.browse(res[0][0])
        return None

    def _find_uom(self, uom_name):
        if not uom_name:
            return None
        uom = self.env['uom.uom'].search([('name', '=ilike', uom_name)], limit=1)
        if not uom:
            uom = self.env['uom.uom'].search([('name', 'ilike', uom_name)], limit=1)
        return uom or None

    # ------------------------------------------------------------------ #
    #  CREAR DOCUMENTO CONTABLE                                            #
    # ------------------------------------------------------------------ #

    def _create_move(self, move_type, data):
        supplier = move_type in ('in_invoice', 'in_refund')
        partner  = self._find_partner(data.get('partner_name', ''), supplier=supplier)

        if move_type in ('out_invoice', 'out_refund'):
            acc_types = ['income', 'income_other']
        else:
            acc_types = ['expense', 'expense_direct_cost']

        default_account = self.env['account.account'].search([
            ('account_type', 'in', acc_types),
            ('company_id', '=', self.env.company.id),
            ('deprecated', '=', False),
        ], limit=1)

        invoice_lines = []
        warnings      = []

        for i, line in enumerate(data.get('lines', []), 1):
            product_name = line.get('product_name', '')
            product      = self._find_product(product_name)
            if not product:
                warnings.append(_('Línea %d: "%s" no encontrado.') % (i, product_name))

            qty   = float(line.get('quantity') or 1)
            price = line.get('price_unit')

            account_id = default_account.id if default_account else False
            if product:
                if move_type in ('out_invoice', 'out_refund'):
                    acc = (product.property_account_income_id
                           or product.categ_id.property_account_income_categ_id)
                else:
                    acc = (product.property_account_expense_id
                           or product.categ_id.property_account_expense_categ_id)
                if acc:
                    account_id = acc.id

            if move_type in ('out_invoice', 'out_refund'):
                tax_field = product.taxes_id if product else self.env['account.tax']
            else:
                tax_field = product.supplier_taxes_id if product else self.env['account.tax']
            taxes = tax_field.filtered(
                lambda t: t.company_id == self.env.company) if product else self.env['account.tax']

            # Precio: del JSON > precio de lista/costo del producto
            if price is not None:
                price_unit = float(price)
            elif product:
                price_unit = (product.lst_price
                              if move_type in ('out_invoice', 'out_refund')
                              else product.standard_price)
            else:
                price_unit = 0.0

            lv = {
                'name':       product.display_name if product else (product_name or _('Servicio')),
                'quantity':   qty,
                'price_unit': price_unit,
                'account_id': account_id,
            }
            if product:
                lv['product_id'] = product.id
            if taxes:
                lv['tax_ids'] = [(6, 0, taxes.ids)]
            invoice_lines.append((0, 0, lv))

        if not invoice_lines:
            raise UserError(_('No hay líneas de producto para crear el documento.'))

        vals = {
            'move_type':        move_type,
            'partner_id':       partner.id,
            'invoice_line_ids': invoice_lines,
        }
        if data.get('invoice_date'):
            vals['invoice_date'] = data['invoice_date']
        if data.get('ref') and move_type in ('in_invoice', 'in_refund'):
            vals['ref'] = data['ref']
        if data.get('notes'):
            vals['narration'] = data['notes']

        move = self.env['account.move'].create(vals)
        if warnings:
            move.message_post(body='<br/>'.join(warnings))

        labels = {
            'out_invoice': _('Factura de Venta'),
            'in_invoice':  _('Factura de Compra'),
            'out_refund':  _('Nota Crédito Venta'),
            'in_refund':   _('Nota Crédito Compra'),
        }
        return {
            'type': 'ir.actions.act_window',
            'name': labels.get(move_type, _('Documento')),
            'res_model': 'account.move',
            'res_id': move.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ------------------------------------------------------------------ #
    #  CREAR COTIZACIÓN / PEDIDO DE VENTA                                  #
    # ------------------------------------------------------------------ #

    def _create_sale_order(self, data):
        partner = self._find_partner(data.get('partner_name', ''))

        order_vals = {'partner_id': partner.id}
        if data.get('validity_date'):
            order_vals['validity_date'] = data['validity_date']
        if data.get('notes'):
            order_vals['note'] = data['notes']

        order = self.env['sale.order'].create(order_vals)
        lines_data = data.get('lines', [])
        if not lines_data:
            order.unlink()
            raise UserError(_('La IA no generó líneas de producto.'))

        warnings = []
        for i, ld in enumerate(lines_data, 1):
            product_name = (ld.get('product_name') or '').strip()
            product      = self._find_product(product_name) if product_name else None
            if not product:
                warnings.append(_('Línea %d: "%s" no encontrado.') % (i, product_name))

            qty   = float(ld.get('quantity') or 1)
            price = ld.get('price_unit')

            uom = self._find_uom(ld.get('uom_name')) if ld.get('uom_name') else None
            if not uom and product:
                uom = product.uom_id
            if not uom:
                uom = self.env.ref('uom.product_uom_unit', raise_if_not_found=False) \
                      or self.env['uom.uom'].search([], limit=1)

            name_line = product.display_name if product else (product_name or _('Producto'))

            if price is not None:
                price_unit = float(price)
            elif product:
                price_unit = product.lst_price or 0.0
            else:
                price_unit = 0.0

            tax_ids = []
            if product and product.taxes_id:
                taxes = product.taxes_id.filtered(lambda t: t.company_id == self.env.company)
                tax_ids = [(6, 0, taxes.ids)]

            lv = {
                'order_id':        order.id,
                'product_id':      product.id if product else False,
                'name':            name_line,
                'product_uom_qty': qty,
                'product_uom':     uom.id,
                'price_unit':      price_unit,
            }
            if tax_ids:
                lv['tax_id'] = tax_ids
            self.env['sale.order.line'].create(lv)

        if warnings:
            order.message_post(body='<br/>'.join(warnings))

        return {
            'type': 'ir.actions.act_window',
            'name': _('Cotización'),
            'res_model': 'sale.order',
            'res_id': order.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ------------------------------------------------------------------ #
    #  CONSULTAS A LA BASE DE DATOS                                        #
    # ------------------------------------------------------------------ #

    def _execute_query(self, data):
        query_type = data.get('query_type', 'invoices')
        filters    = data.get('filters', {})
        question   = data.get('question', self.user_input or '')

        handlers = {
            'invoices':  self._query_invoices,
            'sales':     self._query_sales,
            'purchases': self._query_purchases,
            'partners':  self._query_partners,
            'products':  self._query_products,
            'stock':     self._query_stock,
        }
        results_html = handlers.get(query_type, lambda f: '<p>Tipo no reconocido.</p>')(filters)

        try:
            summary = self.provider_id.call_ai(
                f'El usuario preguntó: "{question}"\n\nDatos de Odoo:\n{results_html}\n\n'
                'Resume los resultados de forma concisa.',
                system_prompt='Analista de datos empresariales. Responde en español, breve y claro.',
            )
        except Exception:
            summary = ''

        self.response_html = (
            '<div style="margin-bottom:12px;padding:10px;background:#e8f4fd;'
            'border-radius:6px;border-left:4px solid #2196F3;">'
            '<strong>🤖 Análisis:</strong><br/>'
            + summary.replace('\n', '<br/>')
            + '</div>' + results_html
        )
        self.ai_response = summary
        return self._reopen()

    def _fmt(self, amount):
        sym = self.env.company.currency_id.symbol or '$'
        return f'{sym} {amount:,.2f}'

    def _html_table(self, headers, rows, title=''):
        th = 'background:#667eea;color:white;padding:8px 10px;text-align:left;'
        td = 'padding:7px 10px;border-bottom:1px solid #e0e0e0;'
        alt= 'background:#f9f9f9;'
        h  = f'<h4 style="margin:12px 0 8px;color:#444;">{title}</h4>' if title else ''
        h += f'<table style="width:100%;border-collapse:collapse;font-size:13px;"><thead><tr>'
        for col in headers:
            h += f'<th style="{th}">{col}</th>'
        h += '</tr></thead><tbody>'
        for i, row in enumerate(rows):
            h += f'<tr style="{alt if i%2==0 else ""}">'
            for cell in row:
                h += f'<td style="{td}">{cell}</td>'
            h += '</tr>'
        if not rows:
            h += f'<tr><td colspan="{len(headers)}" style="{td}color:#999;">Sin resultados</td></tr>'
        return h + '</tbody></table>'

    def _date_domain(self, filters, date_field='invoice_date'):
        d = []
        if filters.get('date_from'):
            d.append((date_field, '>=', filters['date_from']))
        if filters.get('date_to'):
            d.append((date_field, '<=', filters['date_to']))
        if filters.get('state'):
            d.append(('state', '=', filters['state']))
        if filters.get('partner_name'):
            p = self.env['res.partner'].search(
                [('name', 'ilike', filters['partner_name'])], limit=1)
            if p:
                d.append(('partner_id', '=', p.id))
        return d

    def _query_invoices(self, filters):
        domain = [('move_type', 'in', ['out_invoice','out_refund','in_invoice','in_refund'])]
        domain += self._date_domain(filters)
        moves = self.env['account.move'].search(domain, order='invoice_date desc', limit=50)
        state_l = {'draft':'Borrador','posted':'Publicada','cancel':'Cancelada'}
        type_l  = {'out_invoice':'F.Venta','in_invoice':'F.Compra',
                   'out_refund':'NC Venta','in_refund':'NC Compra'}
        rows = [(m.name or '(borrador)', type_l.get(m.move_type,''), m.partner_id.name or '',
                 str(m.invoice_date or ''), state_l.get(m.state, m.state),
                 self._fmt(m.amount_total)) for m in moves]
        total = sum(m.amount_total for m in moves)
        html  = self._html_table(['Número','Tipo','Contacto','Fecha','Estado','Total'],
                                  rows, f'📄 Facturas ({len(moves)})')
        return html + f'<p style="text-align:right;font-weight:bold;">Total: {self._fmt(total)}</p>'

    def _query_sales(self, filters):
        domain = [('state','in',['sale','done'])]
        domain += self._date_domain(filters, 'date_order')
        orders = self.env['sale.order'].search(domain, order='date_order desc', limit=50)
        state_l = {'draft':'Borrador','sent':'Enviado','sale':'Confirmado',
                   'done':'Completado','cancel':'Cancelado'}
        rows = [(o.name, o.partner_id.name or '', str(o.date_order)[:10],
                 state_l.get(o.state, o.state), self._fmt(o.amount_total)) for o in orders]
        total = sum(o.amount_total for o in orders)
        html  = self._html_table(['Pedido','Cliente','Fecha','Estado','Total'],
                                  rows, f'🛒 Pedidos ({len(orders)})')
        return html + f'<p style="text-align:right;font-weight:bold;">Total: {self._fmt(total)}</p>'

    def _query_purchases(self, filters):
        if 'purchase.order' not in self.env:
            return '<p>Módulo de compras no instalado.</p>'
        domain = [('state','in',['purchase','done'])]
        domain += self._date_domain(filters, 'date_order')
        orders = self.env['purchase.order'].search(domain, order='date_order desc', limit=50)
        rows = [(o.name, o.partner_id.name or '', str(o.date_order)[:10],
                 self._fmt(o.amount_total)) for o in orders]
        total = sum(o.amount_total for o in orders)
        html  = self._html_table(['Orden','Proveedor','Fecha','Total'],
                                  rows, f'📦 Compras ({len(orders)})')
        return html + f'<p style="text-align:right;font-weight:bold;">Total: {self._fmt(total)}</p>'

    def _query_partners(self, filters):
        domain = [('active','=',True)]
        if filters.get('partner_name'):
            domain.append(('name','ilike',filters['partner_name']))
        partners = self.env['res.partner'].search(domain, limit=60)
        rows = [(p.name,
                 ('Cliente ' if p.customer_rank > 0 else '') +
                 ('Proveedor' if p.supplier_rank > 0 else ''),
                 p.email or '', p.phone or p.mobile or '', p.city or '')
                for p in partners]
        return self._html_table(['Nombre','Tipo','Email','Teléfono','Ciudad'],
                                 rows, f'👥 Contactos ({len(partners)})')

    def _query_products(self, filters):
        domain = [('active','=',True)]
        if filters.get('partner_name'):
            domain.append(('name','ilike',filters['partner_name']))
        products = self.env['product.product'].search(domain, limit=60)
        rows = [(p.name, p.categ_id.name or '', p.uom_id.name or '',
                 self._fmt(p.lst_price), self._fmt(p.standard_price))
                for p in products]
        return self._html_table(['Producto','Categoría','UOM','Precio','Costo'],
                                 rows, f'📦 Productos ({len(products)})')

    def _query_stock(self, filters):
        if 'stock.quant' not in self.env:
            return '<p>Módulo de inventario no instalado.</p>'
        quants = self.env['stock.quant'].search(
            [('location_id.usage','=','internal')], limit=80)
        rows = [(q.product_id.name, q.location_id.complete_name or '',
                 f'{q.quantity:.2f}', q.product_id.uom_id.name or '')
                for q in quants if q.quantity > 0]
        return self._html_table(['Producto','Ubicación','Cantidad','UOM'],
                                 rows, f'📊 Stock ({len(rows)} items)')

    # ------------------------------------------------------------------ #
    #  CORREO                                                              #
    # ------------------------------------------------------------------ #

    def _compose_email(self, data):
        ctx = {
            'default_subject': data.get('subject', ''),
            'default_body':    data.get('body', '').replace('\n', '<br/>'),
        }
        if self.partner_id:
            ctx['default_partner_ids'] = [self.partner_id.id]
        if self.res_model and self.res_id:
            ctx.update({'default_model': self.res_model,
                        'default_res_id': self.res_id,
                        'default_res_ids': [self.res_id]})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mail.compose.message',
            'view_mode': 'form',
            'target': 'new',
            'context': ctx,
        }
