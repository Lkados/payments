# Copyright (c) 2017, Frappe Technologies and contributors
# License: MIT. See LICENSE

import json
from types import MappingProxyType
from urllib.parse import urlencode

import frappe
import stripe
from frappe import _
from frappe.integrations.utils import create_request_log, make_get_request
from frappe.model.document import Document
from frappe.utils import call_hook_method, cint, flt, get_url

from payments.utils import create_payment_gateway

currency_wise_minimum_charge_amount = {
	"JPY": 50,
	"MXN": 10,
	"DKK": 2.50,
	"HKD": 4.00,
	"NOK": 3.00,
	"SEK": 3.00,
	"USD": 0.50,
	"AUD": 0.50,
	"BRL": 0.50,
	"CAD": 0.50,
	"CHF": 0.50,
	"EUR": 0.50,
	"GBP": 0.30,
	"NZD": 0.50,
	"SGD": 0.50,
}


class StripeSettings(Document):
	supported_currencies = (
		"AED",
		"ALL",
		"ANG",
		"ARS",
		"AUD",
		"AWG",
		"BBD",
		"BDT",
		"BIF",
		"BMD",
		"BND",
		"BOB",
		"BRL",
		"BSD",
		"BWP",
		"BZD",
		"CAD",
		"CHF",
		"CLP",
		"CNY",
		"COP",
		"CRC",
		"CVE",
		"CZK",
		"DJF",
		"DKK",
		"DOP",
		"DZD",
		"EGP",
		"ETB",
		"EUR",
		"FJD",
		"FKP",
		"GBP",
		"GIP",
		"GMD",
		"GNF",
		"GTQ",
		"GYD",
		"HKD",
		"HNL",
		"HRK",
		"HTG",
		"HUF",
		"IDR",
		"ILS",
		"INR",
		"ISK",
		"JMD",
		"JPY",
		"KES",
		"KHR",
		"KMF",
		"KRW",
		"KYD",
		"KZT",
		"LAK",
		"LBP",
		"LKR",
		"LRD",
		"MAD",
		"MDL",
		"MNT",
		"MOP",
		"MRO",
		"MUR",
		"MVR",
		"MWK",
		"MXN",
		"MYR",
		"NAD",
		"NGN",
		"NIO",
		"NOK",
		"NPR",
		"NZD",
		"PAB",
		"PEN",
		"PGK",
		"PHP",
		"PKR",
		"PLN",
		"PYG",
		"QAR",
		"RUB",
		"SAR",
		"SBD",
		"SCR",
		"SEK",
		"SGD",
		"SHP",
		"SLL",
		"SOS",
		"STD",
		"SVC",
		"SZL",
		"THB",
		"TOP",
		"TTD",
		"TWD",
		"TZS",
		"UAH",
		"UGX",
		"USD",
		"UYU",
		"UZS",
		"VND",
		"VUV",
		"WST",
		"XAF",
		"XOF",
		"XPF",
		"YER",
		"ZAR",
	)

	currency_wise_minimum_charge_amount = MappingProxyType(currency_wise_minimum_charge_amount)

	def on_update(self):
		create_payment_gateway(
			"Stripe-" + self.gateway_name,
			settings="Stripe Settings",
			controller=self.gateway_name,
		)
		call_hook_method("payment_gateway_enabled", gateway="Stripe-" + self.gateway_name)
		if not self.flags.ignore_mandatory:
			self.validate_stripe_credentails()

	def validate_stripe_credentails(self):
		if self.publishable_key and self.secret_key:
			header = {
				"Authorization": "Bearer {}".format(
					self.get_password(fieldname="secret_key", raise_exception=False)
				)
			}
			try:
				make_get_request(url="https://api.stripe.com/v1/charges", headers=header)
			except Exception:
				frappe.throw(_("Seems Publishable Key or Secret Key is wrong !!!"))

	def validate_webhook_signature(self, payload, signature, webhook_secret):
		"""Valide la signature du webhook Stripe"""
		try:
			stripe.Webhook.construct_event(payload, signature, webhook_secret)
			return True
		except ValueError:
			frappe.log_error("Invalid payload in Stripe webhook")
			return False
		except stripe.error.SignatureVerificationError:
			frappe.log_error("Invalid signature in Stripe webhook") 
			return False

	def validate_transaction_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(
				_(
					"Please select another payment method. Stripe does not support transactions in currency '{0}'"
				).format(currency)
			)

	def validate_minimum_transaction_amount(self, currency, amount):
		if currency in self.currency_wise_minimum_charge_amount:
			if flt(amount) < self.currency_wise_minimum_charge_amount.get(currency, 0.0):
				frappe.throw(
					_("For currency {0}, the minimum transaction amount should be {1}").format(
						currency, self.currency_wise_minimum_charge_amount.get(currency, 0.0)
					)
				)

	def get_payment_url(self, **kwargs):
		return get_url(f"./stripe_checkout?{urlencode(kwargs)}")

	def create_request(self, data):
		self.data = frappe._dict(data)
		stripe.api_key = self.get_password(fieldname="secret_key", raise_exception=False)
		stripe.default_http_client = stripe.http_client.RequestsClient()

		try:
			self.integration_request = create_request_log(self.data, service_name="Stripe")
			return self.create_charge_on_stripe()

		except Exception:
			frappe.log_error(frappe.get_traceback())
			return {
				"redirect_to": frappe.redirect_to_message(
					_("Server Error"),
					_(
						"It seems that there is an issue with the server's stripe configuration. In case of failure, the amount will get refunded to your account."
					),
				),
				"status": 401,
			}

	def create_charge_on_stripe(self):
		try:
			charge = stripe.Charge.create(
				amount=cint(flt(self.data.amount) * 100),
				currency=self.data.currency,
				source=self.data.stripe_token_id,
				description=self.data.description,
				receipt_email=self.data.payer_email,
			)

			if charge.captured is True:
				self.integration_request.db_set("status", "Completed", update_modified=False)
				self.flags.status_changed_to = "Completed"

			else:
				frappe.log_error(charge.failure_message, "Stripe Payment not completed")

		except Exception:
			frappe.log_error(frappe.get_traceback())

		return self.finalize_request()

	def finalize_request(self):
		redirect_to = self.data.get("redirect_to") or None
		redirect_message = self.data.get("redirect_message") or None
		status = self.integration_request.status

		if self.flags.status_changed_to == "Completed":
			if self.data.reference_doctype and self.data.reference_docname:
				custom_redirect_to = None
				try:
					custom_redirect_to = frappe.get_doc(
						self.data.reference_doctype, self.data.reference_docname
					).run_method("on_payment_authorized", self.flags.status_changed_to)
				except Exception:
					frappe.log_error(frappe.get_traceback())

				if custom_redirect_to:
					redirect_to = custom_redirect_to

				redirect_url = f"payment-success?doctype={self.data.reference_doctype}&docname={self.data.reference_docname}"

			if self.redirect_url:
				redirect_url = self.redirect_url
				redirect_to = None
		else:
			redirect_url = "payment-failed"

		if redirect_to and "?" in redirect_url:
			redirect_url += "&" + urlencode({"redirect_to": redirect_to})
		else:
			redirect_url += "?" + urlencode({"redirect_to": redirect_to})

		if redirect_message:
			redirect_url += "&" + urlencode({"redirect_message": redirect_message})

		return {"redirect_to": redirect_url, "status": status}


def get_gateway_controller(doctype, docname, payment_gateway=None):
	if not payment_gateway:
		reference_doc = frappe.get_doc(doctype, docname)
		payment_gateway = reference_doc.payment_gateway
	gateway_controller = frappe.db.get_value("Payment Gateway", payment_gateway, "gateway_controller")
	return gateway_controller


# ==========================================
# FONCTIONS WEBHOOK STRIPE
# ==========================================

@frappe.whitelist(allow_guest=True, methods=["POST"])
def stripe_webhook_handler():
	"""Handler principal pour les webhooks Stripe"""
	try:
		payload = frappe.local.request.get_data()
		signature = frappe.get_request_header("Stripe-Signature")
		
		# Parser l'événement
		event = json.loads(payload.decode('utf-8'))
		event_type = event.get("type")
		event_id = event.get("id")
		
		# Log de l'événement reçu
		frappe.log_error(f"Stripe webhook received: {event_type} - {event_id}", "Stripe Webhook Received")
		
		# Créer un log de la requête
		create_request_log({
			"event_type": event_type,
			"event_id": event_id,
			"data": event
		}, service_name="Stripe Webhook", name=event_id)
		
		# Traiter les événements de paiement
		if event_type == "charge.succeeded":
			handle_charge_succeeded(event)
		elif event_type == "payment_intent.succeeded":
			handle_payment_intent_succeeded(event)
		elif event_type == "charge.failed":
			handle_charge_failed(event)
		elif event_type == "payment_intent.payment_failed":
			handle_payment_intent_failed(event)
		elif event_type == "invoice.payment_succeeded":
			handle_invoice_payment_succeeded(event)
		elif event_type == "invoice.payment_failed":
			handle_invoice_payment_failed(event)
		
		return {"status": "success", "received": True, "event_type": event_type}
		
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Stripe Webhook Error")
		return {"status": "error", "message": str(e)}


def handle_charge_succeeded(event):
	"""Traite les événements charge.succeeded"""
	try:
		charge_data = event.get("data", {}).get("object", {})
		charge_id = charge_data.get("id")
		amount = charge_data.get("amount", 0) / 100  # Convertir de centimes
		currency = charge_data.get("currency", "").upper()
		
		frappe.log_error(f"Processing charge.succeeded: {charge_id} - {amount} {currency}", "Stripe Charge Success")
		
		# Chercher l'Integration Request correspondante
		integration_requests = frappe.db.sql("""
			SELECT name, reference_doctype, reference_docname, status, data
			FROM `tabIntegration Request`
			WHERE integration_request_service = 'Stripe'
			AND status IN ('Initiated', 'Queued')
			AND (reference_docname LIKE %s OR data LIKE %s)
			ORDER BY creation DESC
			LIMIT 5
		""", (f"%{charge_id}%", f"%{charge_id}%"), as_dict=True)
		
		if integration_requests:
			for req in integration_requests:
				# Mettre à jour le statut de l'Integration Request
				frappe.db.sql("""
					UPDATE `tabIntegration Request` 
					SET status = 'Completed', modified = NOW()
					WHERE name = %s
				""", req.name)
				
				frappe.log_error(f"Updated Integration Request {req.name} to Completed", "Stripe Payment Success")
				
				# Traiter le Payment Request si c'est le bon type
				if req.reference_doctype == "Payment Request" and req.reference_docname:
					process_payment_request(req.reference_docname, charge_id, amount)
				
				# Déclencher les hooks sur le document de référence
				if req.reference_doctype and req.reference_docname:
					try:
						ref_doc = frappe.get_doc(req.reference_doctype, req.reference_docname)
						if hasattr(ref_doc, 'run_method'):
							ref_doc.run_method("on_payment_authorized", "Completed")
						frappe.db.commit()
						frappe.log_error(f"Payment authorized for {req.reference_doctype} {req.reference_docname}", "Payment Hook Success")
					except Exception:
						frappe.log_error(frappe.get_traceback(), "Payment Hook Error")
				break
		else:
			# Si pas d'Integration Request trouvée, chercher par montant et date récente
			recent_requests = frappe.db.sql("""
				SELECT name, reference_doctype, reference_docname, status, data
				FROM `tabIntegration Request`
				WHERE integration_request_service = 'Stripe'
				AND status IN ('Initiated', 'Queued')
				AND DATE(creation) >= DATE_SUB(NOW(), INTERVAL 1 DAY)
				ORDER BY creation DESC
				LIMIT 10
			""", as_dict=True)
			
			for req in recent_requests:
				# Vérifier si le montant correspond dans les données
				if req.data and str(amount) in req.data:
					frappe.db.sql("""
						UPDATE `tabIntegration Request` 
						SET status = 'Completed', modified = NOW()
						WHERE name = %s
					""", req.name)
					
					if req.reference_doctype == "Payment Request" and req.reference_docname:
						process_payment_request(req.reference_docname, charge_id, amount)
					
					frappe.log_error(f"Updated Integration Request {req.name} to Completed (by amount match)", "Stripe Payment Success")
					break
			else:
				frappe.log_error(f"No matching Integration Request found for charge {charge_id}", "Stripe Webhook Warning")
			
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Handle Charge Succeeded Error")


def process_payment_request(payment_request_name, stripe_charge_id, amount):
	"""Traite automatiquement un Payment Request après paiement Stripe réussi"""
	try:
		# 1. Mettre à jour le statut du Payment Request directement en DB
		frappe.db.sql("""
			UPDATE `tabPayment Request` 
			SET status = 'Paid', modified = NOW()
			WHERE name = %s
		""", payment_request_name)
		
		frappe.log_error(f"Payment Request {payment_request_name} updated to Paid", "Payment Request Updated")
		
		# 2. Récupérer les infos du Payment Request
		pr_doc = frappe.get_doc("Payment Request", payment_request_name)
		
		# 3. Créer automatiquement un Payment Entry
		create_automatic_payment_entry(pr_doc, stripe_charge_id)
		
		frappe.db.commit()
		frappe.log_error(f"Payment Request {payment_request_name} fully processed", "Payment Processing Complete")
		
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Process Payment Request Error: {payment_request_name}")


def create_automatic_payment_entry(pr_doc, stripe_charge_id):
	"""Crée automatiquement un Payment Entry pour un Payment Request"""
	try:
		# Vérifier qu'un Payment Entry n'existe pas déjà
		existing_pe = frappe.db.exists("Payment Entry", {
			"party": pr_doc.party,
			"reference_no": stripe_charge_id,
			"docstatus": ["<", 2]
		})
		
		if existing_pe:
			frappe.log_error(f"Payment Entry already exists: {existing_pe}", "Payment Entry Exists")
			return existing_pe
		
		# Créer un nouveau Payment Entry
		pe_doc = frappe.new_doc("Payment Entry")
		pe_doc.payment_type = "Receive"
		pe_doc.party_type = "Customer"
		pe_doc.party = pr_doc.party
		pe_doc.posting_date = frappe.utils.nowdate()
		pe_doc.paid_amount = pr_doc.grand_total
		pe_doc.received_amount = pr_doc.grand_total
		pe_doc.target_exchange_rate = 1
		pe_doc.source_exchange_rate = 1
		pe_doc.reference_no = stripe_charge_id
		pe_doc.reference_date = frappe.utils.nowdate()
		pe_doc.company = pr_doc.company
		pe_doc.mode_of_payment = "Stripe"
		
		# Configuration des comptes
		pe_doc.paid_from = "411000 - CLIENTS DIVERS - JE"  # Compte client
		pe_doc.paid_to = "512900 - Stripe-Stripe - JE"     # Compte Stripe
		
		# Ajouter la référence au document source
		if pr_doc.reference_doctype and pr_doc.reference_name:
			pe_doc.append("references", {
				"reference_doctype": pr_doc.reference_doctype,
				"reference_name": pr_doc.reference_name,
				"allocated_amount": pr_doc.grand_total
			})
		
		# Sauvegarder en draft
		pe_doc.save(ignore_permissions=True)
		frappe.log_error(f"Payment Entry created in draft: {pe_doc.name}", "Payment Entry Created")
		
		# Essayer de soumettre automatiquement
		try:
			pe_doc.submit()
			frappe.log_error(f"Payment Entry submitted: {pe_doc.name}", "Payment Entry Submitted")
		except Exception as submit_error:
			frappe.log_error(f"Could not auto-submit Payment Entry {pe_doc.name}: {str(submit_error)}", "Payment Entry Draft Only")
		
		return pe_doc.name
		
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Create Payment Entry Error for {pr_doc.name}")
		return None


def handle_payment_intent_succeeded(event):
	"""Traite les événements payment_intent.succeeded"""
	try:
		payment_intent_data = event.get("data", {}).get("object", {})
		payment_intent_id = payment_intent_data.get("id")
		amount = payment_intent_data.get("amount", 0) / 100
		currency = payment_intent_data.get("currency", "").upper()
		
		frappe.log_error(f"Processing payment_intent.succeeded: {payment_intent_id} - {amount} {currency}", "Stripe Payment Intent Success")
		
		# Logique similaire pour payment_intent
		integration_requests = frappe.db.sql("""
			SELECT name, reference_doctype, reference_docname, status, data
			FROM `tabIntegration Request`
			WHERE integration_request_service = 'Stripe'
			AND status IN ('Initiated', 'Queued')
			AND (reference_docname LIKE %s OR data LIKE %s)
			ORDER BY creation DESC
			LIMIT 5
		""", (f"%{payment_intent_id}%", f"%{payment_intent_id}%"), as_dict=True)
		
		if integration_requests:
			for req in integration_requests:
				frappe.db.sql("""
					UPDATE `tabIntegration Request` 
					SET status = 'Completed', modified = NOW()
					WHERE name = %s
				""", req.name)
				
				frappe.log_error(f"Updated Integration Request {req.name} to Completed", "Stripe Payment Intent Success")
				
				# Déclencher les hooks
				if req.reference_doctype and req.reference_docname:
					try:
						ref_doc = frappe.get_doc(req.reference_doctype, req.reference_docname)
						ref_doc.run_method("on_payment_authorized", "Completed")
						frappe.db.commit()
						frappe.log_error(f"Payment authorized for {req.reference_doctype} {req.reference_docname}", "Payment Hook Success")
					except Exception:
						frappe.log_error(frappe.get_traceback(), "Payment Hook Error")
				break
					
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Handle Payment Intent Succeeded Error")


def handle_charge_failed(event):
	"""Traite les événements charge.failed"""
	try:
		charge_data = event.get("data", {}).get("object", {})
		charge_id = charge_data.get("id")
		failure_message = charge_data.get("failure_message", "Unknown error")
		
		frappe.log_error(f"Processing charge.failed: {charge_id} - {failure_message}", "Stripe Charge Failed")
		
		# Chercher et mettre à jour l'Integration Request
		integration_requests = frappe.db.sql("""
			SELECT name, reference_doctype, reference_docname, status
			FROM `tabIntegration Request`
			WHERE integration_request_service = 'Stripe'
			AND status IN ('Initiated', 'Queued')
			AND (reference_docname LIKE %s OR data LIKE %s)
			ORDER BY creation DESC
			LIMIT 3
		""", (f"%{charge_id}%", f"%{charge_id}%"), as_dict=True)
		
		if integration_requests:
			for req in integration_requests:
				frappe.db.sql("""
					UPDATE `tabIntegration Request` 
					SET status = 'Failed', error = %s, modified = NOW()
					WHERE name = %s
				""", (failure_message, req.name))
				
				frappe.log_error(f"Updated Integration Request {req.name} to Failed: {failure_message}", "Stripe Payment Failed")
				break
					
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Handle Charge Failed Error")


def handle_payment_intent_failed(event):
	"""Traite les événements payment_intent.payment_failed"""
	try:
		payment_intent_data = event.get("data", {}).get("object", {})
		payment_intent_id = payment_intent_data.get("id")
		last_payment_error = payment_intent_data.get("last_payment_error", {})
		failure_message = last_payment_error.get("message", "Payment failed")
		
		frappe.log_error(f"Processing payment_intent.payment_failed: {payment_intent_id} - {failure_message}", "Stripe Payment Intent Failed")
		
		# Mettre à jour l'Integration Request
		integration_requests = frappe.db.sql("""
			SELECT name, reference_doctype, reference_docname, status
			FROM `tabIntegration Request`
			WHERE integration_request_service = 'Stripe'
			AND status IN ('Initiated', 'Queued')
			AND (reference_docname LIKE %s OR data LIKE %s)
			ORDER BY creation DESC
			LIMIT 3
		""", (f"%{payment_intent_id}%", f"%{payment_intent_id}%"), as_dict=True)
		
		if integration_requests:
			for req in integration_requests:
				frappe.db.sql("""
					UPDATE `tabIntegration Request` 
					SET status = 'Failed', error = %s, modified = NOW()
					WHERE name = %s
				""", (failure_message, req.name))
				
				frappe.log_error(f"Updated Integration Request {req.name} to Failed: {failure_message}", "Stripe Payment Intent Failed")
				break
					
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Handle Payment Intent Failed Error")


def handle_invoice_payment_succeeded(event):
	"""Traite les événements invoice.payment_succeeded (abonnements)"""
	try:
		invoice_data = event.get("data", {}).get("object", {})
		subscription_id = invoice_data.get("subscription")
		invoice_id = invoice_data.get("id")
		
		frappe.log_error(f"Processing invoice.payment_succeeded: {invoice_id} for subscription {subscription_id}", "Stripe Subscription Payment Success")
		
		# Logique pour les abonnements
		call_hook_method("handle_subscription_payment", 
						subscription_id=subscription_id, 
						status="success",
						invoice_data=invoice_data)
					
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Handle Invoice Payment Succeeded Error")


def handle_invoice_payment_failed(event):
	"""Traite les événements invoice.payment_failed (abonnements)"""
	try:
		invoice_data = event.get("data", {}).get("object", {})
		subscription_id = invoice_data.get("subscription")
		invoice_id = invoice_data.get("id")
		
		frappe.log_error(f"Processing invoice.payment_failed: {invoice_id} for subscription {subscription_id}", "Stripe Subscription Payment Failed")
		
		# Logique pour les abonnements
		call_hook_method("handle_subscription_payment", 
						subscription_id=subscription_id, 
						status="failed",
						invoice_data=invoice_data)
					
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Handle Invoice Payment Failed Error")


@frappe.whitelist()
def test_webhook_configuration(gateway_name):
	"""Teste la configuration webhook Stripe"""
	try:
		stripe_settings = frappe.get_doc("Stripe Settings", gateway_name)
		
		if not hasattr(stripe_settings, 'webhook_secret') or not stripe_settings.webhook_secret:
			return {"success": False, "message": "Webhook secret not configured"}
		
		if not stripe_settings.secret_key:
			return {"success": False, "message": "Stripe secret key not configured"}
			
		# Vérifier que l'API Stripe fonctionne
		stripe.api_key = stripe_settings.get_password(fieldname="secret_key", raise_exception=False)
		
		# Test simple
		try:
			endpoints = stripe.WebhookEndpoint.list(limit=10)
			return {
				"success": True, 
				"message": f"Configuration OK. Found {len(endpoints.data)} webhook endpoint(s) in Stripe."
			}
		except Exception as e:
			return {
				"success": False, 
				"message": f"Stripe API error: {str(e)}"
			}
			
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Webhook configuration test failed")
		return {
			"success": False, 
			"message": f"Configuration test failed: {str(e)}"
		}


@frappe.whitelist()
def force_update_integration_request(integration_request_name, status="Completed"):
	"""Force la mise à jour d'une Integration Request (pour debug)"""
	try:
		frappe.db.sql("""
			UPDATE `tabIntegration Request` 
			SET status = %s, modified = NOW()
			WHERE name = %s
		""", (status, integration_request_name))
		
		frappe.db.commit()
		
		return {"success": True, "message": f"Integration Request {integration_request_name} updated to {status}"}
		
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Force Update Integration Request Error")
		return {"success": False, "message": str(e)}


@frappe.whitelist()
def test_payment_processing(payment_request_name, stripe_charge_id="test_charge_123"):
	"""Teste le traitement automatique d'un Payment Request (pour debug)"""
	try:
		# Tester la fonction process_payment_request
		process_payment_request(payment_request_name, stripe_charge_id, 200.45)
		
		return {"success": True, "message": f"Payment Request {payment_request_name} processed successfully"}
		
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Test Payment Processing Error")
		return {"success": False, "message": str(e)}


@frappe.whitelist()
def get_pending_integration_requests():
	"""Récupère les Integration Requests en attente (pour debug)"""
	try:
		pending_requests = frappe.db.sql("""
			SELECT name, reference_doctype, reference_docname, status, creation, data
			FROM `tabIntegration Request`
			WHERE integration_request_service = 'Stripe'
			AND status IN ('Initiated', 'Queued')
			ORDER BY creation DESC
			LIMIT 20
		""", as_dict=True)
		
		return {"success": True, "data": pending_requests}
		
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Pending Integration Requests Error")
		return {"success": False, "message": str(e)}