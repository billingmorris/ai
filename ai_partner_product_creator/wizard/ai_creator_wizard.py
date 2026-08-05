# -*- coding: utf-8 -*-
import json
import re
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# ====================================================================== #
#  SYSTEM PROMPTS                                                          #
# ====================================================================== #

SYSTEM_CUSTOMER = """
Eres un extractor de datos para Odoo 16. El usuario describirá un CLIENTE en lenguaje natural.
Tu única tarea: devolver un JSON válido con los campos encontrados. Sin texto extra. Sin markdown.

Esquema (incluye solo los campos que el usuario mencione, omite los demás):
{
  "name":      "<Razón social o nombre completo>",
  "vat":       "<NIT o cédula, solo dígitos y guiones, ej: 901234567-8>",
  "street":    "<dirección>",
  "city":      "<ciudad>",
  "state":     "<departamento>",
  "zip":       "<código postal>",
  "phone":     "<teléfono fijo>",
  "mobile":    "<celular>",
  "email":     "<correo electrónico>",
  "website":   "<sitio web>",
  "comment":   "<notas adicionales>"
}

Reglas:
- El campo "name" siempre es obligatorio.
- "vat" debe incluir el dígito de verificación si lo mencionan (ej: 901234567-8).
- Si dicen "Medellín, Antioquia" → city: "Medellín", state: "Antioquia".
- No inventes datos que el usuario no mencionó.
""".strip()

SYSTEM_SUPPLIER = """
Eres un extractor de datos para Odoo 16. El usuario describirá un PROVEEDOR en lenguaje natural.
Tu única tarea: devolver un JSON válido. Sin texto extra. Sin markdown.

Esquema:
{
  "name":    "<Razón social>",
  "vat":     "<NIT o cédula>",
  "street":  "<dirección>",
  "city":    "<ciudad>",
  "state":   "<departamento>",
  "zip":     "<código postal>",
  "phone":   "<teléfono>",
  "mobile":  "<celular>",
  "email":   "<correo>",
  "website": "<web>",
  "comment": "<notas>"
}

Reglas:
- "name" siempre obligatorio.
- No inventes datos.
""".strip()

SYSTEM_PRODUCT = """
Eres un extractor de datos para Odoo 16. El usuario describirá un PRODUCTO en lenguaje natural.
Tu única tarea: devolver un JSON válido. Sin texto extra. Sin markdown.

Esquema:
{
  "name":           "<nombre del producto>",
  "default_code":   "<referencia interna>",
  "barcode":        "<código de barras EAN13>",
  "description":    "<descripción interna>",
  "list_price":     <precio de venta como número>,
  "standard_price": <costo como número>,
  "type":           "<'consu' para consumible, 'storable' para almacenable, 'service' para servicio>",
  "categ_name":     "<nombre exacto de la categoría>",
  "uom_name":       "<unidad de medida, ej: 'Unidades', 'kg', 'L'>",
  "taxes_included": <true si el precio incluye IVA, false si no>,
  "sale_ok":        <true si se vende>,
  "purchase_ok":    <true si se compra>
}

Reglas:
- "name" siempre obligatorio.
- Precios siempre como número (28000, no "28.000" ni "$28,000").
- type: usa "storable" para almacenable/inventariable, "consu" para consumible, "service" para servicio.
- No inventes datos que no se mencionaron.
""".strip()


# ====================================================================== #
#  WIZARD                                                                  #
# ====================================================================== #

class AiCreatorWizard(models.TransientModel):
    _name = 'ai.creator.wizard'
    _description = 'Asistente IA — Crear Registros en Lenguaje Natural'

    # ---------- configuración ----------------------------------------- #
    provider_id = fields.Many2one(
        'ai.creator.provider',
        string='Proveedor IA',
        default=lambda self: self._default_provider(),
    )
    record_type = fields.Selection([
        ('customer', '👤 Cliente'),
        ('supplier', '🏭 Proveedor'),
        ('product',  '📦 Producto'),
    ], string='¿Qué quieres crear?', required=True, default='customer')

    # ---------- entrada ----------------------------------------------- #
    instruction = fields.Text(
        string='Instrucción en lenguaje natural',
        required=True,
        placeholder=(
            'Ej: Crear cliente Comercializadora ABC SAS, NIT 901234567-8, '
            'dirección Calle 45 #12-34, Medellín, Antioquia, '
            'correo ventas@abc.com, celular 3001234567'
        ),
    )

    # ---------- resultado --------------------------------------------- #
    state = fields.Selection([
        ('draft',    'Borrador'),
        ('preview',  'Vista Previa'),
        ('done',     'Creado'),
        ('error',    'Error'),
    ], default='draft')

    # campos de previsualización (editables antes de confirmar)
    preview_json   = fields.Text(string='JSON extraído (editable)', readonly=False)
    preview_html   = fields.Html(string='Vista Previa', readonly=True)
    error_message  = fields.Text(string='Error', readonly=True)

    # referencia al registro creado
    res_model = fields.Char(readonly=True)
    res_id    = fields.Integer(readonly=True)
    res_name  = fields.Char(readonly=True)

    # ------------------------------------------------------------------ #
    def _default_provider(self):
        try:
            return self.env['ai.creator.provider'].get_default()
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    #  PASO 1: LLAMAR A LA IA Y MOSTRAR PREVIEW                           #
    # ------------------------------------------------------------------ #

    def action_extract(self):
        """Llama a la IA, extrae el JSON y muestra la vista previa."""
        self.ensure_one()
        if not self.provider_id:
            raise UserError(_('Selecciona un proveedor de IA primero.'))

        system_map = {
            'customer': SYSTEM_CUSTOMER,
            'supplier': SYSTEM_SUPPLIER,
            'product':  SYSTEM_PRODUCT,
        }
        system_prompt = system_map[self.record_type]

        try:
            raw = self.provider_id.call(system_prompt, self.instruction)
        except UserError as e:
            self.state = 'error'
            self.error_message = str(e)
            return self._reopen()

        # Limpiar markdown si viene envuelto
        cleaned = self._strip_markdown(raw)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            self.state = 'error'
            self.error_message = _(
                'La IA no devolvió un JSON válido.\n\nRespuesta recibida:\n%s'
            ) % raw
            return self._reopen()

        # Guardar JSON bonito y generar preview HTML
        self.preview_json = json.dumps(data, ensure_ascii=False, indent=2)
        self.preview_html = self._build_preview_html(data)
        self.state = 'preview'
        return self._reopen()

    # ------------------------------------------------------------------ #
    #  PASO 2: CONFIRMAR Y CREAR EN ODOO                                  #
    # ------------------------------------------------------------------ #

    def action_confirm(self):
        """Lee el JSON (posiblemente editado), crea el registro en Odoo."""
        self.ensure_one()
        try:
            data = json.loads(self.preview_json or '{}')
        except json.JSONDecodeError as e:
            raise UserError(_('El JSON tiene errores de sintaxis:\n%s') % str(e))

        if not data.get('name'):
            raise UserError(_('El campo "name" (nombre) es obligatorio y no fue detectado.'))

        try:
            if self.record_type == 'customer':
                record = self._create_partner(data, is_customer=True, is_supplier=False)
            elif self.record_type == 'supplier':
                record = self._create_partner(data, is_customer=False, is_supplier=True)
            else:
                record = self._create_product(data)
        except Exception as e:
            self._write_log(data, error=str(e))
            raise UserError(_('Error al crear el registro:\n%s') % str(e))

        self.res_model = record._name
        self.res_id    = record.id
        self.res_name  = record.display_name
        self.state     = 'done'

        self._write_log(data)

        return self._reopen()

    def action_open_record(self):
        """Abre el registro recién creado."""
        self.ensure_one()
        if not self.res_model or not self.res_id:
            return
        view_map = {
            'res.partner':      'base.view_partner_form',
            'product.template': 'product.product_template_form_view',
        }
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.res_model,
            'res_id':    self.res_id,
            'view_mode': 'form',
            'target':    'current',
        }

    def action_reset(self):
        """Volver al inicio para crear otro registro."""
        self.state         = 'draft'
        self.preview_json  = False
        self.preview_html  = False
        self.error_message = False
        self.res_model     = False
        self.res_id        = 0
        self.res_name      = False
        self.instruction   = False
        return self._reopen()

    # ------------------------------------------------------------------ #
    #  CREACIÓN DE PARTNER (CLIENTE / PROVEEDOR)                          #
    # ------------------------------------------------------------------ #

    def _create_partner(self, data, is_customer, is_supplier):
        """Crea un res.partner con los datos extraídos por la IA."""
        vals = {
            'name':             data.get('name'),
            'customer_rank':    1 if is_customer else 0,
            'supplier_rank':    1 if is_supplier else 0,
            'company_type':     'company',
            'is_company':       True,
        }

        # Campos simples opcionales
        for field in ('vat', 'street', 'zip', 'phone', 'mobile', 'email',
                      'website', 'comment'):
            if data.get(field):
                vals[field] = data[field]

        # Ciudad
        if data.get('city'):
            vals['city'] = data['city']

        # Departamento / Estado
        if data.get('state'):
            state_rec = self._find_state(data['state'])
            if state_rec:
                vals['state_id'] = state_rec.id
                # País del estado
                vals['country_id'] = state_rec.country_id.id
            else:
                # Si no encontramos el estado, dejamos la ciudad sola
                _logger.warning('Estado/departamento no encontrado: %s', data['state'])

        # País por defecto: Colombia si no se detectó
        if 'country_id' not in vals:
            colombia = self.env['res.country'].search([('code', '=', 'CO')], limit=1)
            if colombia:
                vals['country_id'] = colombia.id

        partner = self.env['res.partner'].create(vals)
        return partner

    def _find_state(self, name):
        """Busca un estado/departamento por nombre (exacto o parcial)."""
        # Intento exacto
        state = self.env['res.country.state'].search(
            [('name', '=ilike', name)], limit=1)
        if not state:
            state = self.env['res.country.state'].search(
                [('name', 'ilike', name)], limit=1)
        return state

    # ------------------------------------------------------------------ #
    #  CREACIÓN DE PRODUCTO                                               #
    # ------------------------------------------------------------------ #

    def _create_product(self, data):
        """Crea un product.template con los datos extraídos por la IA."""

        # Tipo de producto
        type_map = {
            'storable':    'product',   # almacenable en Odoo 16 = 'product'
            'almacenable': 'product',
            'inventariable': 'product',
            'consu':       'consu',
            'consumible':  'consu',
            'service':     'service',
            'servicio':    'service',
        }
        raw_type = (data.get('type') or 'product').lower()
        odoo_type = type_map.get(raw_type, 'product')

        vals = {
            'name':         data['name'],
            'type':         odoo_type,
            'list_price':   float(data.get('list_price') or 0),
            'sale_ok':      bool(data.get('sale_ok', True)),
            'purchase_ok':  bool(data.get('purchase_ok', True)),
        }

        if data.get('default_code'):
            vals['default_code'] = data['default_code']
        if data.get('barcode'):
            vals['barcode'] = data['barcode']
        if data.get('description'):
            vals['description'] = data['description']

        # Costo — se escribe en standard_price después de crear
        standard_price = float(data.get('standard_price') or 0)

        # Categoría
        if data.get('categ_name'):
            categ = self._find_or_create_category(data['categ_name'])
            if categ:
                vals['categ_id'] = categ.id

        # Unidad de medida
        if data.get('uom_name'):
            uom = self._find_uom(data['uom_name'])
            if uom:
                vals['uom_id']    = uom.id
                vals['uom_po_id'] = uom.id

        template = self.env['product.template'].create(vals)

        # Costo: se asigna sobre el product.product (variante)
        if standard_price:
            template.product_variant_ids[:1].write(
                {'standard_price': standard_price}
            )

        return template

    def _find_or_create_category(self, name):
        """Busca la categoría de producto; si no existe, la crea."""
        categ = self.env['product.category'].search(
            [('name', '=ilike', name)], limit=1)
        if not categ:
            categ = self.env['product.category'].search(
                [('name', 'ilike', name)], limit=1)
        if not categ:
            # Crear bajo la categoría raíz
            parent = self.env.ref('product.product_category_all', raise_if_not_found=False)
            categ = self.env['product.category'].create({
                'name': name,
                'parent_id': parent.id if parent else False,
            })
        return categ

    def _find_uom(self, name):
        """Busca unidad de medida por nombre."""
        uom = self.env['uom.uom'].search([('name', '=ilike', name)], limit=1)
        if not uom:
            uom = self.env['uom.uom'].search([('name', 'ilike', name)], limit=1)
        return uom

    # ------------------------------------------------------------------ #
    #  HELPERS                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _strip_markdown(text):
        """Elimina bloques ```json ... ``` que algunos modelos agregan."""
        text = text.strip()
        if '```json' in text:
            text = text.split('```json', 1)[1].split('```', 1)[0]
        elif '```' in text:
            text = text.split('```', 1)[1].split('```', 1)[0]
        return text.strip()

    def _build_preview_html(self, data):
        """Construye una tabla HTML legible con los campos extraídos."""
        label_map = {
            'name':           'Nombre / Razón Social',
            'vat':            'NIT / Cédula',
            'street':         'Dirección',
            'city':           'Ciudad',
            'state':          'Departamento',
            'zip':            'Código Postal',
            'phone':          'Teléfono',
            'mobile':         'Celular',
            'email':          'Correo',
            'website':        'Sitio Web',
            'comment':        'Notas',
            'default_code':   'Referencia',
            'barcode':        'Código de Barras',
            'list_price':     'Precio de Venta',
            'standard_price': 'Costo',
            'type':           'Tipo',
            'categ_name':     'Categoría',
            'uom_name':       'Unidad de Medida',
            'sale_ok':        'Se Vende',
            'purchase_ok':    'Se Compra',
            'description':    'Descripción',
        }
        type_labels = {
            'customer': 'Cliente',
            'supplier': 'Proveedor',
            'product':  'Producto',
        }
        color_map = {
            'customer': '#0d6efd',
            'supplier': '#198754',
            'product':  '#fd7e14',
        }
        color = color_map.get(self.record_type, '#6c757d')
        tipo  = type_labels.get(self.record_type, '')

        rows = ''
        for key, value in data.items():
            if value is None or value == '':
                continue
            label = label_map.get(key, key)
            # Formato booleano
            if isinstance(value, bool):
                value = '✅ Sí' if value else '❌ No'
            rows += (
                f'<tr>'
                f'<td style="padding:6px 12px;font-weight:600;color:#495057;'
                f'white-space:nowrap">{label}</td>'
                f'<td style="padding:6px 12px;color:#212529">{value}</td>'
                f'</tr>'
            )

        html = f"""
<div style="border:2px solid {color};border-radius:8px;overflow:hidden;margin-top:8px">
  <div style="background:{color};color:white;padding:10px 16px;font-weight:700;font-size:14px">
    🔍 Datos extraídos — {tipo}
  </div>
  <table style="width:100%;border-collapse:collapse">
    <tbody>
      {rows}
    </tbody>
  </table>
</div>
<p style="color:#6c757d;font-size:12px;margin-top:8px">
  ✏️ Puedes editar el JSON directamente antes de confirmar la creación.
</p>
"""
        return html

    def _write_log(self, data, error=None):
        self.env['ai.creator.log'].sudo().create({
            'provider_id':  self.provider_id.id,
            'record_type':  self.record_type,
            'instruction':  self.instruction,
            'json_result':  json.dumps(data, ensure_ascii=False, indent=2),
            'res_model':    self.res_model or False,
            'res_id':       self.res_id or 0,
            'res_name':     self.res_name or False,
            'status':       'error' if error else 'success',
            'error_msg':    error or False,
        })

    def _reopen(self):
        return {
            'type':      'ir.actions.act_window',
            'res_model': self._name,
            'res_id':    self.id,
            'view_mode': 'form',
            'target':    'new',
            'context':   self.env.context,
        }
