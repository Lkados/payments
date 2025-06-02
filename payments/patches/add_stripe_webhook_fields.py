# Copyright (c) 2025, Frappe Technologies and Contributors
# License: MIT. See LICENSE

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""Ajoute les champs webhook aux Stripe Settings existants"""
	
	# Vérifier si les champs existent déjà
	meta = frappe.get_meta("Stripe Settings")
	
	if not meta.has_field("webhook_secret"):
		# Ajouter les nouveaux champs via Custom Fields
		custom_fields = {
			"Stripe Settings": [
				{
					"fieldname": "webhook_section",
					"fieldtype": "Section Break",
					"label": "Webhook Configuration",
					"insert_after": "secret_key"
				},
				{
					"fieldname": "webhook_secret",
					"fieldtype": "Password",
					"label": "Webhook Secret",
					"description": "Secret pour valider les webhooks Stripe. Récupérez-le depuis votre dashboard Stripe.",
					"insert_after": "webhook_section"
				},
				{
					"fieldname": "column_break_webhook",
					"fieldtype": "Column Break",
					"insert_after": "webhook_secret"
				},
				{
					"fieldname": "webhook_url_display",
					"fieldtype": "Data",
					"label": "Webhook URL",
					"read_only": 1,
					"description": "Copiez cette URL dans votre dashboard Stripe",
					"insert_after": "column_break_webhook"
				}
			]
		}
		
		try:
			create_custom_fields(custom_fields)
			frappe.db.commit()
			print("✅ Stripe webhook fields added successfully")
		except Exception as e:
			print(f"❌ Error adding Stripe webhook fields: {str(e)}")
			frappe.log_error(frappe.get_traceback(), "Stripe Webhook Migration Error")
	
	# Mettre à jour l'URL webhook pour les enregistrements existants
	try:
		webhook_url = "/api/method/payments.payment_gateways.doctype.stripe_settings.stripe_settings.stripe_webhook_handler"
		
		# Mettre à jour tous les Stripe Settings existants
		stripe_settings = frappe.get_all("Stripe Settings", fields=["name"])
		
		for setting in stripe_settings:
			doc = frappe.get_doc("Stripe Settings", setting.name)
			if not doc.webhook_url_display:
				doc.webhook_url_display = webhook_url
				doc.save(ignore_permissions=True)
		
		frappe.db.commit()
		print(f"✅ Updated webhook URLs for {len(stripe_settings)} Stripe Settings")
		
	except Exception as e:
		print(f"❌ Error updating webhook URLs: {str(e)}")
		frappe.log_error(frappe.get_traceback(), "Stripe Webhook URL Update Error")