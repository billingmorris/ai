# -*- coding: utf-8 -*-
from odoo import fields, models, _


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ai_default_provider_id = fields.Many2one(
        'ai.provider',
        string='Proveedor IA por Defecto',
        config_parameter='ai_assistant.default_provider_id',
    )
    ai_log_enabled = fields.Boolean(
        string='Activar Log de Solicitudes',
        config_parameter='ai_assistant.log_enabled',
    )
    ai_max_tokens_global = fields.Integer(
        string='Máximo de Tokens Global',
        config_parameter='ai_assistant.max_tokens_global',
        default=2000,
    )

    def action_open_ai_providers(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Proveedores de IA'),
            'res_model': 'ai.provider',
            'view_mode': 'tree,form',
            'target': 'current',
        }
