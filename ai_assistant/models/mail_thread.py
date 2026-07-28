# -*- coding: utf-8 -*-
from odoo import api, models, _
from odoo.exceptions import UserError


class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    def action_open_ai_assistant(self):
        """Abre el asistente de IA en contexto del registro actual."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('🤖 Asistente IA'),
            'res_model': 'ai.action.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': self._name,
                'default_res_id': self.id,
                'default_record_name': getattr(self, 'name', str(self.id)),
            },
        }

    def action_ai_compose_email(self):
        """Abre el asistente para redactar un correo con IA."""
        self.ensure_one()
        template = self.env['ai.prompt.template'].search([
            ('action_type', '=', 'send_email'), ('active', '=', True)
        ], limit=1)
        return {
            'type': 'ir.actions.act_window',
            'name': _('📧 Redactar Correo con IA'),
            'res_model': 'ai.action.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': self._name,
                'default_res_id': self.id,
                'default_action_type': 'send_email',
                'default_template_id': template.id if template else False,
                'default_record_name': getattr(self, 'name', str(self.id)),
            },
        }
