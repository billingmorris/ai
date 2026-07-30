# -*- coding: utf-8 -*-
import json
import logging
import unicodedata
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AiActionWizard(models.TransientModel):
    _name = 'ai.action.wizard'
    _description = 'Asistente de Acciones IA'

    # Contexto del registro origen
    res_model   = fields.Char(string='Modelo Origen')
    res_id      = fields.Integer(string='ID Origen')
    record_name = fields.Char(string='Registro')

    # Configuración
    provider_id = fields.Many2one('ai.provider', string='Proveedor',
        default=lambda self: self._get_default_provider())

    action_type = fields.Selection([
        ('create_sale_order',     'Crear Cotización'),
        ('create_out_invoice',    'Crear Factura de Venta'),
        ('create_in_invoice',     'Crear Factura de Compra'),
        ('create_in_refund',      'Crear Nota Crédito Compra'),
        ('create_out_refund',     'Crear Nota Crédito Venta'),
        ('query',                 'Consulta'),
        ('send_email',            'Redactar Correo'),
        ('draft_text',            'Redactar Texto'),
        ('summarize',             'Resumir'),
        ('translate',             'Traducir'),
    ], string='Tipo detectado', readonly=True)

    # Entrada
    user_input = fields.Text(string='Instrucción', required=True,
        placeholder='Ej: Crea una factura de venta para Geotours con 30 unidades de Combo Pastel...')

    # Respuesta
    ai_response   = fields.Text(string='Respuesta IA', readonly=True)
    response_html = fields.Html(string='Vista Previa', readonly=True)
    error_message = fields.Char(string='Error', readonly=True)
    has_attachment = fields.Boolean(compute='_compute_has_attachment')

    # Adjuntos
    attachment_ids = fields.Many2many(
        'ir.attachment', 'ai_wizard_attachment_rel',
        'wizard_id', 'attachment_id',
        string='Adjuntos (imágenes o PDFs)',
    )

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

    def _normalize(self, text):
        text = text.lower().strip()
        text = unicodedata.normalize('NFD', text)
        return ''.join(c for c in text if unicodedata.category(c) != 'Mn')

    # ------------------------------------------------------------------ #
    #  CATÁLOGO PARA EL PROMPT                                             #
    # ------------------------------------------------------------------ #

    def _get_catalog_context(self):
        """
        Genera el catálogo de partners y productos para el prompt.
        Para partners incluye TODOS los nombres por los que se puede conocer
        a cada contacto (name, commercial_company_name, alias) para que la IA
        devuelva el nombre correcto o uno que coincida en la búsqueda.
        """
        cr = self.env.cr
        # Traer id, name, commercial_company_name de todos los clientes/proveedores
        cr.execute("""
            SELECT
                id,
                name,
                COALESCE(commercial_company_name, '') AS commercial,
                COALESCE(ref, '')                     AS ref
            FROM res_partner
            WHERE active = true
              AND is_company = true    -- solo empresas para no saturar con personas
            ORDER BY COALESCE(commercial_company_name, name)
            LIMIT 250
            -- Si necesitas contactos individuales también, quita el filtro is_company
        """)
        rows = cr.fetchall()

        partner_entries = []
        seen_ids = set()
        for pid, name, commercial, ref in rows:
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            # Construir entrada con todos los nombres conocidos
            parts = [name]
            if commercial and commercial.lower() != name.lower():
                parts.append(commercial)
            if ref and ref not in parts:
                parts.append(f'ref:{ref}')
            # Formato: "NOMBRE PRINCIPAL [aka: ALIAS1, ALIAS2]"
            if len(parts) > 1:
                entry = f"{parts[0]} [aka: {', '.join(parts[1:])}]"
            else:
                entry = parts[0]
            partner_entries.append(entry)

        partner_list = '\n  - '.join([''] + partner_entries) or 'ninguno'

        # Productos — nombre, UOM, precio venta, costo
        products = self.env['product.product'].search([('active', '=', True)], limit=200)
        product_lines = []
        for p in products:
            uom   = p.uom_id.name if p.uom_id else ''
            price = p.lst_price or 0.0
            cost  = p.standard_price or 0.0
            product_lines.append(f'  - "{p.name}" | UOM: {uom} | venta: {price} | costo: {cost}')
        product_list = '\n'.join(product_lines) or '  - (sin productos)'

        return partner_list, product_list


    # ------------------------------------------------------------------ #
    #  SYSTEM PROMPT                                                       #
    # ------------------------------------------------------------------ #

    def _get_system_prompt(self):
        partner_list, product_list = self._get_catalog_context()
        return (
            "Eres un asistente empresarial integrado en Odoo 16. Responde SIEMPRE en español.\n\n"
            "Interpreta la instrucción del usuario y devuelve un JSON puro, "
            "sin markdown, sin texto adicional, sin explicaciones.\n\n"
            "El JSON siempre debe incluir el campo 'intent' con uno de estos valores:\n"
            "  create_sale_order | create_out_invoice | create_in_invoice |\n"
            "  create_out_refund | create_in_refund | query |\n"
            "  send_email | draft_text | summarize | translate\n\n"
            "CONTACTOS EN ODOO (nombre, y entre paréntesis nombre comercial o referencia si aplica):\n" + partner_list + "\n\n"
            "PRODUCTOS EN ODOO:\n" + product_list + "\n\n"
            "═══════════════════════════════════════\n"
            "ESQUEMAS JSON POR INTENT:\n\n"

            "intent=create_sale_order (cotización/pedido de venta):\n"
            '{"intent":"create_sale_order","partner_name":"<nombre>","validity_date":"<YYYY-MM-DD|null>",'
            '"notes":"<null>","lines":[{"product_name":"<nombre>","quantity":<n>,"price_unit":<n|null>,"uom_name":"<uom|null>"}]}\n\n'

            "intent=create_out_invoice (factura de venta al cliente):\n"
            '{"intent":"create_out_invoice","partner_name":"<nombre>","invoice_date":"<YYYY-MM-DD|null>",'
            '"notes":"<null>","lines":[{"product_name":"<nombre>","quantity":<n>,"price_unit":<n|null>}]}\n\n'

            "intent=create_in_invoice (factura de compra/proveedor):\n"
            '{"intent":"create_in_invoice","partner_name":"<proveedor>","invoice_date":"<YYYY-MM-DD|null>",'
            '"ref":"<numero factura proveedor|null>","notes":"<null>",'
            '"lines":[{"product_name":"<nombre>","quantity":<n>,"price_unit":<n>}]}\n\n'

            "intent=create_out_refund (nota crédito de venta):\n"
            '{"intent":"create_out_refund","partner_name":"<cliente>","invoice_date":"<YYYY-MM-DD|null>",'
            '"notes":"<motivo>","lines":[{"product_name":"<nombre>","quantity":<n>,"price_unit":<n>}]}\n\n'

            "intent=create_in_refund (nota crédito de compra):\n"
            '{"intent":"create_in_refund","partner_name":"<proveedor>","invoice_date":"<YYYY-MM-DD|null>",'
            '"notes":"<motivo>","lines":[{"product_name":"<nombre>","quantity":<n>,"price_unit":<n>}]}\n\n'

            "intent=query (consulta/búsqueda en Odoo — NO crea nada):\n"
            '{"intent":"query","query_type":"<tipo>","filters":{"partner_name":"<opt>","date_from":"<opt>",'
            '"date_to":"<opt>","state":"<opt>"},"question":"<pregunta original>"}\n'
            "query_type puede ser: invoices | sales | purchases | partners | products | stock\n\n"

            "intent=send_email:\n"
            '{"intent":"send_email","subject":"<asunto>","body":"<cuerpo>"}\n\n'

            "intent=draft_text|summarize|translate:\n"
            '{"intent":"draft_text","text":"<texto generado>"}\n\n'

            "Reglas:\n"
            "- SOLO el JSON, nada más.\n"
            "- Si cliente/producto no están en la lista, usa el nombre tal como lo escribió el usuario.\n"
            "- Cantidades y precios son números, nunca strings.\n"
            "- Para facturas de venta usa create_out_invoice. Para facturas de compra usa create_in_invoice.\n"
            "- Si el usuario pregunta por datos (cuánto vendí, qué facturas hay, cuántos clientes) usa intent=query."
        )

    # ------------------------------------------------------------------ #
    #  LLAMADA A LA IA                                                     #
    # ------------------------------------------------------------------ #

    def action_call_ai(self):
        self.ensure_one()
        if not self.provider_id:
            raise UserError(_('Selecciona un proveedor de IA.'))

        try:
            response = self.provider_id.call_ai(
                self.user_input,
                system_prompt=self._get_system_prompt(),
                attachments=self.attachment_ids,
            )
            self.ai_response   = response
            self.error_message = False

            # Detectar intent para mostrar en el campo
            try:
                data   = self._parse_json_from(response)
                intent = data.get('intent', '')
                self.action_type = intent if intent in dict(
                    self._fields['action_type'].selection) else False
            except Exception:
                self.action_type = False

            # Preview
            escaped = response.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            self.response_html = (
                '<div style="font-family:monospace;padding:12px;background:#f4f6f9;'
                'border-radius:6px;white-space:pre-wrap;font-size:13px;">'
                + escaped + '</div>'
            )
        except UserError as e:
            self.error_message = str(e)
            self.ai_response   = False

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

        data   = self._parse_json()
        intent = data.get('intent', 'draft_text')

        dispatch = {
            'create_sale_order':  self._create_sale_order,
            'create_out_invoice': lambda: self._create_invoice('out_invoice'),
            'create_in_invoice':  lambda: self._create_invoice('in_invoice'),
            'create_out_refund':  lambda: self._create_invoice('out_refund'),
            'create_in_refund':   lambda: self._create_invoice('in_refund'),
            'send_email':         self._compose_email,
            'query':              self._execute_query,
        }
        handler = dispatch.get(intent)
        if handler:
            return handler()

        # Texto plano
        text = data.get('text', self.ai_response)
        self.response_html = (
            '<div style="padding:12px;background:#f4f6f9;border-radius:6px;'
            'white-space:pre-wrap;">' +
            text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;') +
            '</div>'
        )
        return self._reopen_wizard()

    # ------------------------------------------------------------------ #
    #  PARSEO JSON                                                         #
    # ------------------------------------------------------------------ #

    def _parse_json_from(self, text):
        text = (text or '').strip()
        if '```json' in text:
            text = text.split('```json', 1)[1].split('```', 1)[0]
        elif '```' in text:
            text = text.split('```', 1)[1].split('```', 1)[0]
        return json.loads(text.strip())

    def _parse_json(self):
        try:
            return self._parse_json_from(self.ai_response)
        except json.JSONDecodeError as e:
            raise UserError(
                _('La IA no devolvió un JSON válido.\nDetalle: %s\n\nRespuesta:\n%s')
                % (str(e), self.ai_response)
            )

    # ------------------------------------------------------------------ #
    #  BÚSQUEDAS EN ODOO                                                   #
    # ------------------------------------------------------------------ #

    def _find_partner(self, name, supplier=False):
        """
        Búsqueda robusta de partner en múltiples pasos.
        Cada paso es más permisivo que el anterior.
        Registra en log qué estrategia funcionó para facilitar debug.
        """
        if not name:
            raise UserError(_('La IA no especificó un contacto.'))

        name = name.strip()
        cr   = self.env.cr
        Partner = self.env['res.partner']

        # Filtro base SQL
        rank_filter = ""  # Sin filtro de rank — buscar en todos los contactos

        def search_orm(domain, label):
            # Sin filtro de rank — buscar en TODOS los contactos activos
            base = [('active', '=', True)]
            p = Partner.search(base + domain, limit=1)
            if p:
                _logger.info('[AI Partner] Encontrado por %s: "%s" -> id=%d "%s"', label, name, p.id, p.name)
            return p

        # ── 1. Exacto en name ──────────────────────────────────────────
        p = search_orm([('name', '=ilike', name)], 'name exacto')
        if p: return p

        # ── 2. Exacto en commercial_company_name ───────────────────────
        p = search_orm([('commercial_company_name', '=ilike', name)], 'commercial exacto')
        if p: return p

        # ── 3. Parcial en name ─────────────────────────────────────────
        p = search_orm([('name', 'ilike', name)], 'name parcial')
        if p: return p

        # ── 4. Parcial en commercial_company_name ──────────────────────
        p = search_orm([('commercial_company_name', 'ilike', name)], 'commercial parcial')
        if p: return p

        # ── 5. Parcial en display_name (incluye empresa padre) ─────────
        p = search_orm([('display_name', 'ilike', name)], 'display_name')
        if p: return p

        # ── 6. Por ref interna ─────────────────────────────────────────
        p = search_orm([('ref', 'ilike', name)], 'ref')
        if p: return p

        # ── 7. Palabras individuales (umbral: 2 chars) ─────────────────
        words = [w for w in name.split() if len(w) >= 2]
        for word in words:
            p = search_orm(['|', '|',
                ('name', 'ilike', word),
                ('commercial_company_name', 'ilike', word),
                ('display_name', 'ilike', word),
            ], f'palabra "{word}"')
            if p: return p

        # ── 8. SQL ILIKE sobre todos los campos de texto del partner ───
        try:
            cr.execute("""
                SELECT id FROM res_partner
                WHERE active = true
                  %s
                  AND (
                    name                    ILIKE '%%%%' || %%s || '%%%%'
                    OR commercial_company_name ILIKE '%%%%' || %%s || '%%%%'
                    OR display_name          ILIKE '%%%%' || %%s || '%%%%'
                    OR ref                   ILIKE '%%%%' || %%s || '%%%%'
                    OR email                 ILIKE '%%%%' || %%s || '%%%%'
                    OR vat                   ILIKE '%%%%' || %%s || '%%%%'
                    OR website              ILIKE '%%%%' || %%s || '%%%%'
                  )
                ORDER BY (customer_rank + supplier_rank) DESC
                LIMIT 1
            """ % rank_filter, [name, name, name, name, name, name, name])
            row = cr.fetchone()
            if row:
                p = Partner.browse(row[0])
                _logger.info('[AI Partner] Encontrado por SQL amplio: "%s" -> id=%d "%s"', name, p.id, p.name)
                return p
        except Exception as e:
            _logger.warning('[AI Partner] Error SQL amplio: %s', e)

        # ── 9. Similitud trigrama PostgreSQL (tolera typos) ────────────
        try:
            cr.execute("""
                SELECT id,
                       GREATEST(
                           similarity(lower(name), lower(%%s)),
                           similarity(lower(COALESCE(commercial_company_name,'')), lower(%%s)),
                           similarity(lower(COALESCE(display_name,'')), lower(%%s))
                       ) AS sim
                FROM res_partner
                WHERE active = true %s
                ORDER BY sim DESC
                LIMIT 1
            """ % rank_filter, [name, name, name])
            row = cr.fetchone()
            if row and row[1] and row[1] > 0.08:
                p = Partner.browse(row[0])
                _logger.info('[AI Partner] Encontrado por trigrama (sim=%.0f%%): "%s" -> "%s"',
                             row[1]*100, name, p.name)
                return p
        except Exception as e:
            _logger.warning('[AI Partner] pg_trgm no disponible: %s', e)

        # ── 10. name_search nativo de Odoo ────────────────────────────
        results = Partner.name_search(name, limit=3)
        if results:
            p = Partner.browse(results[0][0])
            _logger.info('[AI Partner] Encontrado por name_search: "%s" -> "%s"', name, p.name)
            return p

        # ── 11. Sin filtro de rank (cualquier contacto activo) ─────────
        for word in words:
            p = Partner.search([('active', '=', True), '|',
                ('name', 'ilike', word),
                ('commercial_company_name', 'ilike', word),
            ], limit=1)
            if p:
                _logger.info('[AI Partner] Encontrado sin rank por "%s": %s', word, p.name)
                return p

        # ── Sin resultado ──────────────────────────────────────────────
        # Sugerir los más cercanos para el mensaje de error
        cr.execute("""
            SELECT name FROM res_partner
            WHERE active = true
            ORDER BY similarity(lower(name), lower(%s)) DESC
            LIMIT 3
        """, [name])
        suggestions = [r[0] for r in cr.fetchall()] if cr.rowcount else []
        suggestion_text = (
            ('\n\nContactos similares: ' + ', '.join(suggestions)) if suggestions else ''
        )
        raise UserError(
            _('No se encontró el contacto "%s" en Odoo.%s') % (name, suggestion_text)
        )


    def _find_product(self, name):
        if not name:
            return None
        name    = name.strip()
        norm    = self._normalize(name)
        Product = self.env['product.product']
        domain  = [('active', '=', True)]

        # 1. Exacto
        p = Product.search(domain + [('name', '=ilike', name)], limit=1)
        if p: return p

        # 2. Parcial completo
        p = Product.search(domain + [('name', 'ilike', name)], limit=1)
        if p: return p

        # 3. Todas las palabras AND (umbral 2 chars)
        words = [w for w in name.split() if len(w) >= 2]
        if words:
            d = list(domain)
            for w in words:
                d.append(('name', 'ilike', w))
            p = Product.search(d, limit=1)
            if p: return p

        # 4. OR con scoring — mejor candidato
        if words:
            or_domain = list(domain)
            clauses = [('name', 'ilike', w) for w in words]
            if len(clauses) > 1:
                or_domain += ['|'] * (len(clauses) - 1)
            or_domain += clauses
            candidates = Product.search(or_domain, limit=30)
            if candidates:
                best = max(candidates, key=lambda prod: sum(
                    1 for w in words if w.lower() in self._normalize(prod.name)
                ))
                score = sum(1 for w in words if w.lower() in self._normalize(best.name))
                if score > 0:
                    _logger.info('Producto por scoring (%d/%d palabras): %s', score, len(words), best.name)
                    return best

        # 5. Similitud PostgreSQL
        try:
            self.env.cr.execute(
                "SELECT id, similarity(lower(name), lower(%s)) AS sim "
                "FROM product_product WHERE active = true "
                "ORDER BY sim DESC LIMIT 1",
                [norm]
            )
            row = self.env.cr.fetchone()
            if row and row[1] > 0.2:
                p = Product.browse(row[0])
                _logger.info('Producto por similitud (%.0f%%): %s', row[1]*100, p.name)
                return p
        except Exception as e:
            _logger.warning('pg_trgm no disponible para productos: %s', e)

        # 6. name_search nativo
        results = Product.name_search(name, limit=1)
        if results:
            return Product.browse(results[0][0])

        return None

    def _find_uom(self, uom_name):
        if not uom_name:
            return None
        uom = self.env['uom.uom'].search([('name', '=ilike', uom_name)], limit=1)
        if not uom:
            uom = self.env['uom.uom'].search([('name', 'ilike', uom_name)], limit=1)
        return uom or None

    # ------------------------------------------------------------------ #
    #  CREAR COTIZACIÓN / PEDIDO DE VENTA                                  #
    # ------------------------------------------------------------------ #

    def _build_sale_line_vals(self, order, line_data, index):
        product_name = (line_data.get('product_name') or '').strip()
        product      = self._find_product(product_name) if product_name else None
        warning      = None
        qty          = float(line_data.get('quantity') or 1)
        price        = line_data.get('price_unit')

        uom = None
        if line_data.get('uom_name'):
            uom = self._find_uom(line_data['uom_name'])
        if not uom and product:
            uom = product.uom_id
        if not uom:
            uom = self.env.ref('uom.product_uom_unit', raise_if_not_found=False)
            if not uom:
                uom = self.env['uom.uom'].search([], limit=1)

        name = (product.display_name if product else product_name) or _('Producto/Servicio')

        if price is not None:
            price_unit = float(price)
        elif product:
            price_unit = product.with_context(
                pricelist=order.pricelist_id.id if order.pricelist_id else False
            ).lst_price
        else:
            price_unit = 0.0

        tax_ids = []
        if product and product.taxes_id:
            taxes = product.taxes_id.filtered(lambda t: t.company_id == self.env.company)
            tax_ids = [(6, 0, taxes.ids)]

        vals = {
            'order_id':        order.id,
            'product_id':      product.id if product else False,
            'name':            name,
            'product_uom_qty': qty,
            'product_uom':     uom.id,
            'price_unit':      price_unit,
        }
        if tax_ids:
            vals['tax_id'] = tax_ids
        if not product:
            warning = _('Línea %d: "%s" no encontrado, se agregó como descripción.') % (index, product_name)
        return vals, warning

    def _create_sale_order(self):
        data    = self._parse_json()
        partner = self._find_partner(data.get('partner_name', ''))

        order_vals = {'partner_id': partner.id}
        if data.get('validity_date'):
            order_vals['validity_date'] = data['validity_date']
        if data.get('notes'):
            order_vals['note'] = data['notes']

        order    = self.env['sale.order'].create(order_vals)
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
    #  CREAR FACTURA (venta, compra, notas crédito)                        #
    # ------------------------------------------------------------------ #

    def _create_invoice(self, move_type='out_invoice'):
        data    = self._parse_json()
        # Para facturas de compra buscar también entre proveedores
        supplier = move_type in ('in_invoice', 'in_refund')
        partner  = self._find_partner(data.get('partner_name', ''), supplier=supplier)

        # Cuenta contable por defecto según tipo
        if move_type in ('out_invoice', 'out_refund'):
            account_types = ['income', 'income_other']
        else:
            account_types = ['expense', 'expense_direct_cost']

        default_account = self.env['account.account'].search([
            ('account_type', 'in', account_types),
            ('company_id', '=', self.env.company.id),
            ('deprecated', '=', False),
        ], limit=1)

        invoice_lines = []
        warnings      = []

        for i, line in enumerate(data.get('lines', []), 1):
            product_name = line.get('product_name', '')
            product      = self._find_product(product_name)

            if not product:
                warnings.append(
                    _('Línea %d: "%s" no encontrado, se usó descripción libre.') % (i, product_name)
                )

            qty   = float(line.get('quantity') or 1)
            price = float(line.get('price_unit') or 0)

            # Determinar cuenta contable
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

            # Impuestos
            if move_type in ('out_invoice', 'out_refund'):
                tax_field = product.taxes_id if product else self.env['account.tax']
            else:
                tax_field = product.supplier_taxes_id if product else self.env['account.tax']
            taxes = tax_field.filtered(lambda t: t.company_id == self.env.company) if product else self.env['account.tax']

            line_vals = {
                'name':       product.display_name if product else product_name,
                'quantity':   qty,
                'price_unit': price if price else (
                    product.lst_price if (product and move_type in ('out_invoice', 'out_refund'))
                    else (product.standard_price if product else 0.0)
                ),
                'account_id': account_id,
            }
            if product:
                line_vals['product_id'] = product.id
            if taxes:
                line_vals['tax_ids'] = [(6, 0, taxes.ids)]

            invoice_lines.append((0, 0, line_vals))

        if not invoice_lines:
            raise UserError(_('La IA no generó ninguna línea para el documento.'))

        invoice_vals = {
            'move_type':        move_type,
            'partner_id':       partner.id,
            'invoice_line_ids': invoice_lines,
        }
        if data.get('invoice_date'):
            invoice_vals['invoice_date'] = data['invoice_date']
        if data.get('notes'):
            invoice_vals['narration'] = data['notes']
        if data.get('ref') and move_type in ('in_invoice', 'in_refund'):
            invoice_vals['ref'] = data['ref']  # número de factura del proveedor

        invoice = self.env['account.move'].create(invoice_vals)

        if warnings:
            invoice.message_post(body='<br/>'.join(warnings))

        type_labels = {
            'out_invoice': _('Factura de Venta'),
            'in_invoice':  _('Factura de Compra'),
            'out_refund':  _('Nota Crédito Venta'),
            'in_refund':   _('Nota Crédito Compra'),
        }
        return {
            'type': 'ir.actions.act_window',
            'name': type_labels.get(move_type, _('Documento contable')),
            'res_model': 'account.move',
            'res_id': invoice.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ------------------------------------------------------------------ #
    #  CONSULTAS A LA BASE DE DATOS                                        #
    # ------------------------------------------------------------------ #

    def _execute_query(self):
        data       = self._parse_json()
        query_type = data.get('query_type', 'invoices')
        filters    = data.get('filters', {})
        question   = data.get('question', self.user_input)

        results_html = ''

        if query_type == 'invoices':
            results_html = self._query_invoices(filters)
        elif query_type == 'sales':
            results_html = self._query_sales(filters)
        elif query_type == 'purchases':
            results_html = self._query_purchases(filters)
        elif query_type == 'partners':
            results_html = self._query_partners(filters)
        elif query_type == 'products':
            results_html = self._query_products(filters)
        elif query_type == 'stock':
            results_html = self._query_stock(filters)
        else:
            results_html = '<p>Tipo de consulta no reconocido: %s</p>' % query_type

        # Pedir a la IA que interprete los resultados
        summary_prompt = (
            'El usuario preguntó: "%s"\n\n'
            'Estos son los datos de Odoo en HTML:\n%s\n\n'
            'Resume los resultados de forma clara y responde la pregunta del usuario.'
        ) % (question, results_html)

        try:
            summary = self.provider_id.call_ai(
                summary_prompt,
                system_prompt='Eres un analista de datos empresariales. Responde en español, de forma concisa y clara.',
            )
        except Exception:
            summary = ''

        # Mostrar tabla + resumen IA
        self.response_html = (
            '<div style="margin-bottom:16px;padding:12px;background:#e8f4fd;'
            'border-radius:6px;border-left:4px solid #2196F3;">'
            '<strong>🤖 Análisis IA:</strong><br/>' +
            summary.replace('\n', '<br/>') +
            '</div>' + results_html
        )
        self.ai_response = summary

        return self._reopen_wizard()

    def _build_html_table(self, headers, rows, title=''):
        style_table = 'width:100%;border-collapse:collapse;font-size:13px;'
        style_th    = 'background:#667eea;color:white;padding:8px 10px;text-align:left;'
        style_td    = 'padding:7px 10px;border-bottom:1px solid #e0e0e0;'
        style_tr_alt = 'background:#f9f9f9;'

        html = ''
        if title:
            html += f'<h4 style="margin:12px 0 8px;color:#444;">{title}</h4>'
        html += f'<table style="{style_table}"><thead><tr>'
        for h in headers:
            html += f'<th style="{style_th}">{h}</th>'
        html += '</tr></thead><tbody>'
        for i, row in enumerate(rows):
            tr_style = style_tr_alt if i % 2 == 0 else ''
            html += f'<tr style="{tr_style}">'
            for cell in row:
                html += f'<td style="{style_td}">{cell}</td>'
            html += '</tr>'
        if not rows:
            html += f'<tr><td colspan="{len(headers)}" style="{style_td}color:#999;">Sin resultados</td></tr>'
        html += '</tbody></table>'
        return html

    def _parse_date_filters(self, filters):
        domain = []
        if filters.get('date_from'):
            domain.append(('invoice_date', '>=', filters['date_from']))
        if filters.get('date_to'):
            domain.append(('invoice_date', '<=', filters['date_to']))
        if filters.get('state'):
            domain.append(('state', '=', filters['state']))
        if filters.get('partner_name'):
            partner = self.env['res.partner'].search(
                [('name', 'ilike', filters['partner_name'])], limit=1)
            if partner:
                domain.append(('partner_id', '=', partner.id))
        return domain

    def _fmt_currency(self, amount):
        currency = self.env.company.currency_id
        symbol   = currency.symbol or '$'
        return f'{symbol} {amount:,.2f}'

    def _query_invoices(self, filters):
        domain = [('move_type', 'in', ['out_invoice', 'out_refund', 'in_invoice', 'in_refund'])]
        domain += self._parse_date_filters(filters)
        moves = self.env['account.move'].search(domain, order='invoice_date desc', limit=50)

        state_labels = {'draft': 'Borrador', 'posted': 'Publicada', 'cancel': 'Cancelada'}
        type_labels  = {
            'out_invoice': 'Fact. Venta', 'in_invoice': 'Fact. Compra',
            'out_refund': 'NC Venta', 'in_refund': 'NC Compra',
        }
        rows = [(
            m.name or '(borrador)',
            type_labels.get(m.move_type, m.move_type),
            m.partner_id.name or '',
            str(m.invoice_date or ''),
            state_labels.get(m.state, m.state),
            self._fmt_currency(m.amount_total),
        ) for m in moves]

        total = sum(m.amount_total for m in moves if m.move_type in ('out_invoice', 'in_invoice'))
        headers = ['Número', 'Tipo', 'Contacto', 'Fecha', 'Estado', 'Total']
        html = self._build_html_table(headers, rows,
            title=f'📄 Facturas ({len(moves)} resultados)')
        html += f'<p style="text-align:right;font-weight:bold;margin-top:8px;">Total: {self._fmt_currency(total)}</p>'
        return html

    def _query_sales(self, filters):
        domain = [('state', 'in', ['sale', 'done'])]
        if filters.get('partner_name'):
            p = self.env['res.partner'].search([('name', 'ilike', filters['partner_name'])], limit=1)
            if p:
                domain.append(('partner_id', '=', p.id))
        if filters.get('date_from'):
            domain.append(('date_order', '>=', filters['date_from']))
        if filters.get('date_to'):
            domain.append(('date_order', '<=', filters['date_to']))

        orders = self.env['sale.order'].search(domain, order='date_order desc', limit=50)
        rows = [(
            o.name, o.partner_id.name or '',
            str(o.date_order)[:10],
            {'draft':'Borrador','sent':'Enviado','sale':'Confirmado','done':'Completado','cancel':'Cancelado'}.get(o.state, o.state),
            self._fmt_currency(o.amount_total),
        ) for o in orders]

        total = sum(o.amount_total for o in orders)
        html  = self._build_html_table(
            ['Pedido', 'Cliente', 'Fecha', 'Estado', 'Total'], rows,
            title=f'🛒 Pedidos de Venta ({len(orders)} resultados)')
        html += f'<p style="text-align:right;font-weight:bold;margin-top:8px;">Total: {self._fmt_currency(total)}</p>'
        return html

    def _query_purchases(self, filters):
        if 'purchase.order' not in self.env:
            return '<p>Módulo de compras no instalado.</p>'
        domain = [('state', 'in', ['purchase', 'done'])]
        if filters.get('partner_name'):
            p = self.env['res.partner'].search([('name', 'ilike', filters['partner_name'])], limit=1)
            if p:
                domain.append(('partner_id', '=', p.id))
        if filters.get('date_from'):
            domain.append(('date_order', '>=', filters['date_from']))
        if filters.get('date_to'):
            domain.append(('date_order', '<=', filters['date_to']))

        orders = self.env['purchase.order'].search(domain, order='date_order desc', limit=50)
        rows = [(
            o.name, o.partner_id.name or '',
            str(o.date_order)[:10],
            self._fmt_currency(o.amount_total),
        ) for o in orders]

        total = sum(o.amount_total for o in orders)
        html  = self._build_html_table(
            ['Orden', 'Proveedor', 'Fecha', 'Total'], rows,
            title=f'📦 Órdenes de Compra ({len(orders)} resultados)')
        html += f'<p style="text-align:right;font-weight:bold;margin-top:8px;">Total: {self._fmt_currency(total)}</p>'
        return html

    def _query_partners(self, filters):
        domain = [('active', '=', True)]
        if filters.get('partner_name'):
            domain.append(('name', 'ilike', filters['partner_name']))
        partners = self.env['res.partner'].search(domain, limit=50)
        rows = [(
            p.name,
            'Cliente' if p.customer_rank > 0 else '' + ' / Proveedor' if p.supplier_rank > 0 else '',
            p.email or '',
            p.phone or p.mobile or '',
            p.city or '',
        ) for p in partners]
        return self._build_html_table(
            ['Nombre', 'Tipo', 'Email', 'Teléfono', 'Ciudad'], rows,
            title=f'👥 Contactos ({len(partners)} resultados)')

    def _query_products(self, filters):
        domain = [('active', '=', True)]
        if filters.get('partner_name'):  # reutilizar para búsqueda por nombre
            domain.append(('name', 'ilike', filters['partner_name']))
        products = self.env['product.product'].search(domain, limit=50)
        rows = [(
            p.name,
            p.categ_id.name or '',
            p.uom_id.name or '',
            self._fmt_currency(p.lst_price),
            self._fmt_currency(p.standard_price),
        ) for p in products]
        return self._build_html_table(
            ['Producto', 'Categoría', 'UOM', 'Precio Venta', 'Costo'], rows,
            title=f'📦 Productos ({len(products)} resultados)')

    def _query_stock(self, filters):
        if 'stock.quant' not in self.env:
            return '<p>Módulo de inventario no instalado.</p>'
        domain = [('location_id.usage', '=', 'internal')]
        quants = self.env['stock.quant'].search(domain, limit=60)
        rows = [(
            q.product_id.name,
            q.location_id.complete_name or '',
            f'{q.quantity:.2f}',
            q.product_id.uom_id.name or '',
        ) for q in quants if q.quantity > 0]
        return self._build_html_table(
            ['Producto', 'Ubicación', 'Cantidad', 'UOM'], rows,
            title=f'📊 Stock ({len(rows)} items)')

    # ------------------------------------------------------------------ #
    #  CORREO                                                              #
    # ------------------------------------------------------------------ #

    def _compose_email(self):
        data    = self._parse_json()
        subject = data.get('subject', '')
        body    = data.get('body', '').replace('\n', '<br/>')

        ctx = {'default_subject': subject, 'default_body': body}
        if self.res_model and self.res_id:
            ctx.update({
                'default_model':   self.res_model,
                'default_res_id':  self.res_id,
                'default_res_ids': [self.res_id],
            })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mail.compose.message',
            'view_mode': 'form',
            'target': 'new',
            'context': ctx,
        }
