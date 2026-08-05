# -*- coding: utf-8 -*-
import json
import logging
import requests
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AiCreatorProvider(models.Model):
    _name = 'ai.creator.provider'
    _description = 'Proveedor IA para Creator'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    is_default = fields.Boolean(string='Por Defecto')

    provider_type = fields.Selection([
        ('openai',     'OpenAI (ChatGPT)'),
        ('anthropic',  'Anthropic (Claude)'),
        ('deepseek',   'DeepSeek'),
    ], required=True, default='anthropic')

    api_key   = fields.Char(required=True)
    api_url   = fields.Char(compute='_compute_defaults', store=True, readonly=False)
    model_name = fields.Char(compute='_compute_defaults', store=True, readonly=False)
    max_tokens = fields.Integer(default=1500)
    timeout    = fields.Integer(default=60)

    @api.depends('provider_type')
    def _compute_defaults(self):
        _URLS = {
            'openai':    'https://api.openai.com/v1/chat/completions',
            'anthropic': 'https://api.anthropic.com/v1/messages',
            'deepseek':  'https://api.deepseek.com/v1/chat/completions',
        }
        _MODELS = {
            'openai':    'gpt-4o',
            'anthropic': 'claude-sonnet-4-6',
            'deepseek':  'deepseek-chat',
        }
        for r in self:
            r.api_url    = _URLS.get(r.provider_type, '')
            r.model_name = _MODELS.get(r.provider_type, '')

    @api.constrains('is_default')
    def _single_default(self):
        for r in self:
            if r.is_default:
                self.search([('is_default', '=', True), ('id', '!=', r.id)]).write({'is_default': False})

    # ------------------------------------------------------------------ #
    def _headers(self):
        self.ensure_one()
        if self.provider_type == 'anthropic':
            return {
                'x-api-key': self.api_key,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            }
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

    def _payload(self, system_prompt, user_prompt):
        self.ensure_one()
        if self.provider_type == 'anthropic':
            return {
                'model': self.model_name,
                'max_tokens': self.max_tokens,
                'system': system_prompt,
                'messages': [{'role': 'user', 'content': user_prompt}],
            }
        return {
            'model': self.model_name,
            'max_tokens': self.max_tokens,
            'temperature': 0,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user',   'content': user_prompt},
            ],
        }

    def _parse(self, data):
        self.ensure_one()
        if self.provider_type == 'anthropic':
            blocks = data.get('content', [])
            return ''.join(b.get('text', '') for b in blocks if b.get('type') == 'text')
        choices = data.get('choices', [{}])
        return choices[0].get('message', {}).get('content', '')

    def call(self, system_prompt, user_prompt):
        """Llama al API y devuelve el texto de respuesta."""
        self.ensure_one()
        try:
            resp = requests.post(
                self.api_url,
                headers=self._headers(),
                json=self._payload(system_prompt, user_prompt),
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            raise UserError(_('Timeout al conectar con %s.') % self.name)
        except requests.exceptions.ConnectionError:
            raise UserError(_('No se pudo conectar con %s.') % self.name)
        except requests.exceptions.HTTPError as e:
            detail = ''
            try:
                detail = resp.json().get('error', {}).get('message', '')
            except Exception:
                pass
            raise UserError(_('Error HTTP %s: %s') % (self.name, detail or str(e)))

        return self._parse(resp.json()).strip()

    # ------------------------------------------------------------------ #
    @api.model
    def get_default(self):
        p = self.search([('is_default', '=', True), ('active', '=', True)], limit=1)
        if not p:
            p = self.search([('active', '=', True)], limit=1)
        if not p:
            raise UserError(_(
                'No hay ningún proveedor de IA configurado.\n'
                'Ve a AI Creator → Configuración → Proveedores.'
            ))
        return p

    def action_test(self):
        self.ensure_one()
        try:
            r = self.call('Responde solo: "OK"', 'Test de conexión')
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {'title': '✅ Conexión OK', 'message': r, 'type': 'success'},
            }
        except UserError as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {'title': '❌ Error', 'message': str(e), 'type': 'danger', 'sticky': True},
            }
