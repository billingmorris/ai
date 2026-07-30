# -*- coding: utf-8 -*-
from odoo import fields, models


class AiLog(models.Model):
    _name = 'ai.log'
    _description = 'Registro de Llamadas a IA'
    _order = 'create_date desc'

    provider_id = fields.Many2one('ai.provider', string='Proveedor', ondelete='set null')
    model = fields.Char(string='Modelo')
    prompt = fields.Text(string='Prompt Enviado')
    response = fields.Text(string='Respuesta')
    tokens_used = fields.Integer(string='Tokens Usados')
    action_type = fields.Char(string='Tipo de Acción')
    res_model = fields.Char(string='Modelo Origen')
    res_id = fields.Integer(string='ID Origen')
    user_id = fields.Many2one('res.users', string='Usuario',
        default=lambda self: self.env.user)
    create_date = fields.Datetime(string='Fecha', readonly=True)
    duration = fields.Float(string='Duración (seg)')
    status = fields.Selection([
        ('success', 'Exitoso'),
        ('error', 'Error'),
    ], default='success')
    error_message = fields.Text(string='Mensaje de Error')
