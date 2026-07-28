/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState } from "@odoo/owl";

/**
 * Widget de botón de acceso rápido al Asistente IA
 * Se puede añadir en cualquier vista con:
 *   <widget name="ai_assistant_button"/>
 */
class AiAssistantButton extends Component {
    setup() {
        this.action = useService("action");
        this.state = useState({ loading: false });
    }

    async openAssistant() {
        this.state.loading = true;
        try {
            await this.action.doAction({
                type: "ir.actions.act_window",
                name: "🤖 Asistente IA",
                res_model: "ai.action.wizard",
                view_mode: "form",
                target: "new",
                context: {
                    default_res_model: this.props.resModel,
                    default_res_id: this.props.resId,
                },
            });
        } finally {
            this.state.loading = false;
        }
    }
}

AiAssistantButton.template = "ai_assistant.AssistantButton";
AiAssistantButton.props = {
    resModel: { type: String, optional: true },
    resId: { type: Number, optional: true },
};

registry.category("view_widgets").add("ai_assistant_button", {
    component: AiAssistantButton,
});


/**
 * Utilidad global para llamar al asistente desde cualquier lugar
 */
const aiAssistantService = {
    dependencies: ["action", "orm"],
    start(env, { action, orm }) {
        return {
            /**
             * Abre el wizard de IA en contexto de un registro
             */
            async openForRecord(resModel, resId, actionType = "draft_text") {
                return action.doAction({
                    type: "ir.actions.act_window",
                    name: "🤖 Asistente IA",
                    res_model: "ai.action.wizard",
                    view_mode: "form",
                    target: "new",
                    context: {
                        default_res_model: resModel,
                        default_res_id: resId,
                        default_action_type: actionType,
                    },
                });
            },
        };
    },
};

registry.category("services").add("ai_assistant", aiAssistantService);
