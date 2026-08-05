# -*- coding: utf-8 -*-
from odoo import fields, models


class AiCreatorLog(models.Model):
    _name = 'ai.creator.log'
    _description = 'Log de Creaciones con IA'
    _order = 'create_date desc'

    provider_id  = fields.Many2one('ai.creator.provider', string='Proveedor', ondelete='set null')
    record_type  = fields.Selection([
        ('customer', 'Cliente'),
        ('supplier', 'Proveedor'),
        ('product',  'Producto'),
    ], string='Tipo')
    instruction  = fields.Text(string='Instrucción del usuario')
    json_result  = fields.Text(string='JSON extraído por la IA')
    res_model    = fields.Char(string='Modelo creado')
    res_id       = fields.Integer(string='ID creado')
    res_name     = fields.Char(string='Nombre del registro')
    status       = fields.Selection([
        ('success', 'Exitoso'),
        ('error',   'Error'),
    ], default='success')
    error_msg    = fields.Text(string='Error')
    user_id      = fields.Many2one('res.users', default=lambda s: s.env.user)
    create_date  = fields.Datetime(readonly=True)
