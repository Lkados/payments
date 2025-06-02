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
# NOUVELLES FONCTIONS WEBHOOK
# ==========================================

@frappe.whitelist(allow_guest=True, methods=["POST"])
def stripe_webhook_handler():
	"""Handler pour les webhooks Stripe"""
	try:
		payload = frappe.local.request.get_data()
		signature = frappe.get_request_header("Stripe-Signature")
		
		if not signature:
			frappe.throw("Missing Stripe signature")
			
		# Récupérer le webhook secret depuis les settings
		webhook_secret = frappe.db.get_single_value("Stripe Settings", "webhook_secret")
		
		if not webhook_secret:
			frappe.log_error("Webhook secret not configured")
			return {"status": "error", "message": "Webhook secret not configured"}
			
		# Valider la signature
		doc = frappe.get_doc("Stripe Settings")
		if not doc.validate_webhook_signature(payload, signature, webhook_secret):
			frappe.throw("Invalid webhook signature")
			
		# Parser l'événement
		event = json.loads(payload.decode('utf-8'))
		
		# Log de l'événement
		create_request_log({
			"event_type": event.get("type"),
			"event_id": event.get("id"),
			"data": event
		}, service_name="Stripe Webhook")
		
		# Traiter selon le type d'événement
		handle_stripe_event(event)
		
		return {"status": "success"}
		
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Stripe Webhook Error")
		frappe.throw("Webhook processing failed")


def handle_stripe_event(event):
	"""Traite les différents types d'événements Stripe"""
	event_type = event.get("type")
	data = event.get("data", {}).get("object", {})
	
	if event_type == "payment_intent.succeeded":
		handle_payment_success(data)
		
	elif event_type == "payment_intent.payment_failed":
		handle_payment_failure(data)
		
	elif event_type == "invoice.payment_succeeded":
		handle_subscription_payment_success(data)
		
	elif event_type == "invoice.payment_failed":
		handle_subscription_payment_failure(data)
		
	elif event_type == "customer.subscription.deleted":
		handle_subscription_cancelled(data)
		
	elif event_type == "charge.dispute.created":
		handle_dispute_created(data)
	
	# Enqueue pour traitement asynchrone si nécessaire
	frappe.enqueue(
		method="payments.payment_gateways.doctype.stripe_settings.stripe_settings.process_webhook_event",
		queue="long",
		timeout=600,
		is_async=True,
		event_type=event_type,
		event_data=data
	)


def handle_payment_success(payment_data):
	"""Gère les paiements réussis"""
	payment_intent_id = payment_data.get("id")
	
	# Chercher la Integration Request correspondante
	integration_request = frappe.db.get_value(
		"Integration Request",
		{"reference_docname": payment_intent_id},
		"name"
	)
	
	if integration_request:
		doc = frappe.get_doc("Integration Request", integration_request)
		
		# Mettre à jour le statut
		doc.db_set("status", "Completed", update_modified=False)
		
		# Déclencher les hooks de paiement autorisé
		if doc.reference_doctype and doc.reference_docname:
			try:
				ref_doc = frappe.get_doc(doc.reference_doctype, doc.reference_docname)
				ref_doc.run_method("on_payment_authorized", "Completed")
				frappe.db.commit()
			except Exception:
				frappe.log_error(frappe.get_traceback())


def handle_payment_failure(payment_data):
	"""Gère les échecs de paiement"""
	payment_intent_id = payment_data.get("id")
	
	integration_request = frappe.db.get_value(
		"Integration Request", 
		{"reference_docname": payment_intent_id},
		"name"
	)
	
	if integration_request:
		doc = frappe.get_doc("Integration Request", integration_request)
		doc.db_set("status", "Failed", update_modified=False)
		
		# Déclencher les hooks d'échec
		if doc.reference_doctype and doc.reference_docname:
			try:
				ref_doc = frappe.get_doc(doc.reference_doctype, doc.reference_docname)
				ref_doc.run_method("on_payment_failed", "Failed")
				frappe.db.commit()
			except Exception:
				frappe.log_error(frappe.get_traceback())


def handle_subscription_payment_success(invoice_data):
	"""Gère les paiements d'abonnement réussis"""
	subscription_id = invoice_data.get("subscription")
	
	# Logique pour les abonnements
	call_hook_method("handle_subscription_payment", 
					subscription_id=subscription_id, 
					status="success",
					invoice_data=invoice_data)


def handle_subscription_payment_failure(invoice_data):
	"""Gère les échecs de paiement d'abonnement"""
	subscription_id = invoice_data.get("subscription")
	
	call_hook_method("handle_subscription_payment", 
					subscription_id=subscription_id, 
					status="failed",
					invoice_data=invoice_data)


def handle_subscription_cancelled(subscription_data):
	"""Gère l'annulation d'abonnements"""
	subscription_id = subscription_data.get("id")
	
	call_hook_method("handle_subscription_cancelled", 
					subscription_id=subscription_id,
					subscription_data=subscription_data)


def handle_dispute_created(dispute_data):
	"""Gère la création de disputes/chargebacks"""
	charge_id = dispute_data.get("charge")
	
	call_hook_method("handle_payment_dispute", 
					charge_id=charge_id,
					dispute_data=dispute_data)


def process_webhook_event(event_type, event_data):
	"""Traitement asynchrone des événements webhook"""
	try:
		# Traitement supplémentaire si nécessaire
		# Par exemple : envoi d'emails, mise à jour de rapports, etc.
		
		if event_type in ["payment_intent.succeeded", "invoice.payment_succeeded"]:
			# Envoyer email de confirmation
			send_payment_confirmation_email(event_data)
			
		elif event_type in ["payment_intent.payment_failed", "invoice.payment_failed"]:
			# Envoyer notification d'échec
			send_payment_failure_notification(event_data)
			
		elif event_type == "charge.dispute.created":
			# Notifier les disputes
			send_dispute_notification(event_data)
			
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Webhook async processing failed: {event_type}")


def send_payment_confirmation_email(payment_data):
	"""Envoie un email de confirmation de paiement"""
	# Implémentation de l'envoi d'email
	pass


def send_payment_failure_notification(payment_data):
	"""Envoie une notification d'échec de paiement"""
	# Implémentation de la notification
	pass


def send_dispute_notification(dispute_data):
	"""Envoie une notification de dispute"""
	# Implémentation de la notification de dispute
	pass


@frappe.whitelist()
def test_webhook_configuration(gateway_name):
	"""Teste la configuration webhook Stripe"""
	try:
		stripe_settings = frappe.get_doc("Stripe Settings", gateway_name)
		
		if not stripe_settings.webhook_secret:
			return {"success": False, "message": "Webhook secret not configured"}
		
		if not stripe_settings.secret_key:
			return {"success": False, "message": "Stripe secret key not configured"}
			
		# Vérifier que l'API Stripe fonctionne
		stripe.api_key = stripe_settings.get_password(fieldname="secret_key", raise_exception=False)
		
		# Test simple : lister les webhooks endpoints
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