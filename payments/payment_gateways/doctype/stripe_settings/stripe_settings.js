// Copyright (c) 2017, Frappe Technologies and contributors
// For license information, please see license.txt

frappe.ui.form.on("Stripe Settings", {
  refresh: function (frm) {
    // Générer automatiquement l'URL webhook
    if (frm.doc.gateway_name && !frm.doc.webhook_url_display) {
      let webhook_url = `/api/method/payments.payment_gateways.doctype.stripe_settings.stripe_settings.stripe_webhook_handler`;

      // Si on a accès à l'URL du site
      if (window.location.origin) {
        webhook_url = window.location.origin + webhook_url;
      }

      frm.set_value("webhook_url_display", webhook_url);
    }

    // Ajouter un bouton pour copier l'URL webhook
    if (frm.doc.webhook_url_display) {
      frm.add_custom_button(
        __("Copy Webhook URL"),
        function () {
          navigator.clipboard
            .writeText(frm.doc.webhook_url_display)
            .then(function () {
              frappe.show_alert({
                message: __("Webhook URL copied to clipboard!"),
                indicator: "green",
              });
            })
            .catch(function () {
              // Fallback pour les navigateurs qui ne supportent pas clipboard API
              let textArea = document.createElement("textarea");
              textArea.value = frm.doc.webhook_url_display;
              document.body.appendChild(textArea);
              textArea.select();
              document.execCommand("copy");
              document.body.removeChild(textArea);

              frappe.show_alert({
                message: __("Webhook URL copied to clipboard!"),
                indicator: "green",
              });
            });
        },
        __("Actions")
      );
    }

    // Ajouter un bouton pour tester les webhooks
    if (frm.doc.webhook_secret && frm.doc.secret_key) {
      frm.add_custom_button(
        __("Test Webhook Configuration"),
        function () {
          test_webhook_configuration(frm);
        },
        __("Actions")
      );
    }

    // Afficher des informations utiles sur les webhooks
    if (frm.doc.webhook_url_display && !frm.doc.webhook_secret) {
      frm.dashboard.add_comment(
        __(`
        <strong>⚠️ Webhook Configuration Required</strong><br>
        1. Copy the webhook URL: <code>${frm.doc.webhook_url_display}</code><br>
        2. Go to your <a href="https://dashboard.stripe.com/webhooks" target="_blank">Stripe Dashboard → Webhooks</a><br>
        3. Create a new endpoint with this URL<br>
        4. Select these events: payment_intent.succeeded, payment_intent.payment_failed, invoice.payment_succeeded, invoice.payment_failed, customer.subscription.deleted<br>
        5. Copy the webhook secret and paste it in the field above
      `),
        "yellow"
      );
    } else if (frm.doc.webhook_secret) {
      frm.dashboard.add_comment(
        __(`
        <strong>✅ Webhook Configuration Complete</strong><br>
        Your Stripe webhooks are properly configured and will handle payment status updates automatically.
      `),
        "green"
      );
    }
  },

  gateway_name: function (frm) {
    // Régénérer l'URL webhook quand le nom de la gateway change
    if (frm.doc.gateway_name) {
      let webhook_url = `/api/method/payments.payment_gateways.doctype.stripe_settings.stripe_settings.stripe_webhook_handler`;

      if (window.location.origin) {
        webhook_url = window.location.origin + webhook_url;
      }

      frm.set_value("webhook_url_display", webhook_url);
    }
  },

  webhook_secret: function (frm) {
    // Rafraîchir l'affichage quand le webhook secret est ajouté
    if (frm.doc.webhook_secret) {
      frm.refresh();
    }
  },
});

function test_webhook_configuration(frm) {
  frappe.show_alert({
    message: __("Testing webhook configuration..."),
    indicator: "orange",
  });

  // Appeler une méthode pour vérifier la configuration webhook
  frappe.call({
    method:
      "payments.payment_gateways.doctype.stripe_settings.stripe_settings.test_webhook_configuration",
    args: {
      gateway_name: frm.doc.gateway_name,
    },
    callback: function (r) {
      if (r.message && r.message.success) {
        frappe.show_alert({
          message: __("Webhook configuration is working correctly!"),
          indicator: "green",
        });
      } else {
        frappe.show_alert({
          message: __(
            "Webhook configuration test failed. Please check your settings."
          ),
          indicator: "red",
        });
      }
    },
    error: function () {
      frappe.show_alert({
        message: __("Could not test webhook configuration."),
        indicator: "red",
      });
    },
  });
}
