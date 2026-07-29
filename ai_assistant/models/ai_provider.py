# -*- coding: utf-8 -*-
import json
import logging
import requests
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class AiProvider(models.Model):
    _name = 'ai.provider'
    _description = 'Proveedor de IA'
    _order = 'sequence, name'

    name = fields.Char(string='Nombre', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    provider_type = fields.Selection([
        ('openai', 'OpenAI (ChatGPT)'),
        ('anthropic', 'Anthropic (Claude)'),
        ('deepseek', 'DeepSeek'),
        ('custom', 'API Personalizada'),
    ], string='Proveedor', required=True, default='openai')

    api_key = fields.Char(string='API Key', required=True)
    api_url = fields.Char(string='URL del API', compute='_compute_api_url', store=True, readonly=False)
    model_name = fields.Char(string='Modelo', compute='_compute_model_name', store=True, readonly=False)
    max_tokens = fields.Integer(string='Máx. Tokens', default=2000)
    temperature = fields.Float(string='Temperatura', default=0.7, help='0=determinístico, 1=creativo')
    timeout = fields.Integer(string='Timeout (seg)', default=60)
    is_default = fields.Boolean(string='Proveedor por Defecto')

    # Estadísticas
    total_requests = fields.Integer(string='Total Solicitudes', readonly=True)
    total_tokens = fields.Integer(string='Total Tokens', readonly=True)
    last_used = fields.Datetime(string='Último Uso', readonly=True)

    @api.depends('provider_type')
    def _compute_api_url(self):
        urls = {
            'openai': 'https://api.openai.com/v1/chat/completions',
            'anthropic': 'https://api.anthropic.com/v1/messages',
            'deepseek': 'https://api.deepseek.com/v1/chat/completions',
            'custom': '',
        }
        for rec in self:
            if not rec.api_url or rec.api_url in urls.values():
                rec.api_url = urls.get(rec.provider_type, '')

    @api.depends('provider_type')
    def _compute_model_name(self):
        models_map = {
            'openai': 'gpt-4o',
            'anthropic': 'claude-sonnet-4-6',
            'deepseek': 'deepseek-chat',
            'custom': '',
        }
        for rec in self:
            if not rec.model_name or rec.model_name in models_map.values():
                rec.model_name = models_map.get(rec.provider_type, '')

    @api.constrains('is_default')
    def _check_single_default(self):
        for rec in self:
            if rec.is_default:
                others = self.search([('is_default', '=', True), ('id', '!=', rec.id)])
                others.write({'is_default': False})

    def _build_headers(self):
        self.ensure_one()
        if self.provider_type == 'anthropic':
            return {
                'x-api-key': self.api_key,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            }
        else:
            return {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
            }

    def _build_payload(self, messages, system_prompt=None):
        self.ensure_one()
        if self.provider_type == 'anthropic':
            payload = {
                'model': self.model_name,
                'max_tokens': self.max_tokens,
                'messages': messages,
            }
            if system_prompt:
                payload['system'] = system_prompt
        else:
            # OpenAI / DeepSeek / compatible
            all_messages = []
            if system_prompt:
                all_messages.append({'role': 'system', 'content': system_prompt})
            all_messages.extend(messages)
            payload = {
                'model': self.model_name,
                'messages': all_messages,
                'max_tokens': self.max_tokens,
                'temperature': self.temperature,
            }
        return payload

    def _parse_response(self, response_data):
        self.ensure_one()
        try:
            if self.provider_type == 'anthropic':
                content = response_data.get('content', [])
                text = ' '.join(c.get('text', '') for c in content if c.get('type') == 'text')
                tokens = response_data.get('usage', {})
                total = tokens.get('input_tokens', 0) + tokens.get('output_tokens', 0)
            else:
                choices = response_data.get('choices', [{}])
                text = choices[0].get('message', {}).get('content', '')
                usage = response_data.get('usage', {})
                total = usage.get('total_tokens', 0)
            return text.strip(), total
        except Exception as e:
            _logger.error("Error parseando respuesta IA: %s", e)
            return '', 0

    def call_ai(self, prompt, system_prompt=None, context_messages=None):
        """
        Método principal para llamar al proveedor de IA.
        :param prompt: texto del usuario
        :param system_prompt: instrucciones del sistema (rol)
        :param context_messages: historial de mensajes [{role, content}]
        :return: texto de respuesta
        """
        self.ensure_one()
        messages = context_messages or []
        messages.append({'role': 'user', 'content': prompt})

        headers = self._build_headers()
        payload = self._build_payload(messages, system_prompt)

        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.Timeout:
            raise UserError(_('Tiempo de espera agotado al conectar con %s.') % self.name)
        except requests.exceptions.ConnectionError:
            raise UserError(_('No se pudo conectar con %s. Verifique su conexión.') % self.name)
        except requests.exceptions.HTTPError as e:
            error_detail = ''
            try:
                error_detail = response.json().get('error', {}).get('message', '')
            except Exception:
                pass
            raise UserError(_('Error del API %s: %s %s') % (self.name, str(e), error_detail))

        text, tokens_used = self._parse_response(data)

        # Actualizar estadísticas
        self.sudo().write({
            'total_requests': self.total_requests + 1,
            'total_tokens': self.total_tokens + tokens_used,
            'last_used': fields.Datetime.now(),
        })

        # Registrar en log
        self.env['ai.log'].sudo().create({
            'provider_id': self.id,
            'prompt': prompt,
            'response': text,
            'tokens_used': tokens_used,
            'model': self.model_name,
        })

        return text

    def action_test_connection(self):
        self.ensure_one()
        try:
            result = self.call_ai(
                'Responde únicamente con: "Conexión exitosa"',
                system_prompt='Eres un asistente de prueba.'
            )
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('✅ Conexión exitosa'),
                    'message': result,
                    'type': 'success',
                    'sticky': False,
                }
            }
        except UserError as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('❌ Error de conexión'),
                    'message': str(e),
                    'type': 'danger',
                    'sticky': True,
                }
            }

    def action_view_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Logs — %s' % self.name,
            'res_model': 'ai.log',
            'view_mode': 'tree,form',
            'domain': [('provider_id', '=', self.id)],
            'context': {'default_provider_id': self.id},
        }

    @api.model
    def get_default_provider(self):
        provider = self.search([('is_default', '=', True), ('active', '=', True)], limit=1)
        if not provider:
            provider = self.search([('active', '=', True)], limit=1)
        if not provider:
            raise UserError(_('No hay ningún proveedor de IA configurado. Ve a Configuración > IA.'))
        return provider
