import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def send_email(to_email: str | None, subject: str, body: str) -> bool:
    settings = get_settings()
    if not to_email:
        return False
    if not settings.smtp_enabled:
        logger.info("SMTP is disabled; skipped email to %s", to_email)
        return False
    if not settings.smtp_username or not settings.smtp_password:
        logger.warning("SMTP is enabled but username or password is missing")
        return False

    from_email = settings.smtp_from_email or settings.smtp_username
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.smtp_from_name} <{from_email}>"
    message["To"] = to_email
    message.set_content(body)

    try:
        with smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
        ) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP authentication failed. Check the SMTP username and app password.")
        return False
    except smtplib.SMTPException:
        logger.exception("Failed to send email to %s", to_email)
        return False
    except OSError:
        logger.exception("Could not connect to SMTP server for %s", to_email)
        return False

    return True


def send_email_with_attachment(
    to_email: str | None,
    subject: str,
    body: str,
    attachment_bytes: bytes,
    attachment_filename: str,
    attachment_mime_type: str = "application/pdf",
) -> bool:
    settings = get_settings()
    if not to_email:
        return False
    if not settings.smtp_enabled:
        logger.info("SMTP is disabled; skipped email with attachment to %s", to_email)
        return False
    if not settings.smtp_username or not settings.smtp_password:
        logger.warning("SMTP is enabled but username or password is missing")
        return False

    from_email = settings.smtp_from_email or settings.smtp_username
    maintype, subtype = attachment_mime_type.split("/", 1)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.smtp_from_name} <{from_email}>"
    message["To"] = to_email
    message.set_content(body)
    message.add_attachment(
        attachment_bytes,
        maintype=maintype,
        subtype=subtype,
        filename=attachment_filename,
    )

    try:
        with smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
        ) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP authentication failed. Check the SMTP username and app password.")
        return False
    except smtplib.SMTPException:
        logger.exception("Failed to send email with attachment to %s", to_email)
        return False
    except OSError:
        logger.exception("Could not connect to SMTP server for %s", to_email)
        return False

    return True


def email_status() -> dict:
    settings = get_settings()
    from_email = settings.smtp_from_email or settings.smtp_username
    return {
        "enabled": settings.smtp_enabled,
        "configured": bool(
            settings.smtp_enabled
            and settings.smtp_host
            and settings.smtp_port
            and settings.smtp_username
            and settings.smtp_password
            and from_email
        ),
        "host": settings.smtp_host,
        "port": settings.smtp_port,
        "from_email": from_email,
        "username_set": bool(settings.smtp_username),
        "password_set": bool(settings.smtp_password),
    }


def send_test_email(to_email: str) -> bool:
    body = """Hello,

This is a test email from the Job Card Automation System application.

If you received this, SMTP is configured correctly.

Regards,
Job Card Automation System
"""
    return send_email(to_email, "Job Card Automation System test email", body)


def send_user_welcome_email(to_email: str, full_name: str, role: str, is_bootstrap_admin: bool) -> bool:
    account_type = "administrator" if role == "admin" else "user"
    first_line = (
        "Your administrator account has been created."
        if is_bootstrap_admin
        else f"Your {account_type} account has been created."
    )
    body = f"""Hello {full_name},

{first_line}

You can now sign in to the Job Card Automation System application using this email address.

If this account was created for you by an admin, use the temporary password shared by the admin and change it once password management is added.

Regards,
Job Card Automation System
"""
    return send_email(to_email, "Job Card Automation System account created", body)


def send_worker_welcome_email(to_email: str, full_name: str) -> bool:
    body = f"""Hello {full_name},

Your worker profile has been added to the Job Card Automation System application.

The garage can now assign work to you and keep your contact details in the system.

Regards,
Job Card Automation System
"""
    return send_email(to_email, "Job Card Automation System worker profile created", body)


def send_customer_welcome_email(
    to_email: str,
    full_name: str,
    car_registration: str,
    car_model: str,
) -> bool:
    body = f"""Hello {full_name},

Thank you for trusting us with your vehicle.

We have added your customer profile for {car_registration} ({car_model}) in the Job Card Automation System application.

We will use this email address for job updates, quotations, and invoices where applicable.

Regards,
Job Card Automation System
"""
    return send_email(to_email, "Welcome to Job Card Automation System", body)


def send_job_complete_email(
    to_email: str | None,
    full_name: str,
    car_registration: str,
    job_number: str,
) -> bool:
    body = f"""Hello {full_name},

The work on your vehicle {car_registration} is now complete.

Your job card {job_number} has been marked as complete, and the car is ready.

Thank you for trusting us with your vehicle.

Regards,
Job Card Automation System
"""
    return send_email(to_email, f"Your vehicle {car_registration} is ready", body)


def send_password_reset_email(
    to_email: str | None,
    full_name: str,
    reset_url: str,
    ttl_minutes: int,
) -> bool:
    body = f"""Hello {full_name},

We received a request to reset your Job Card Automation System password.

Open this link to set a new password:
{reset_url}

This link expires in {ttl_minutes} minutes and can only be used once.

If you did not request this, you can ignore this email. Your password will
stay the same and no one can use this link without your email account.

Regards,
Job Card Automation System
"""
    return send_email(to_email, "Reset your Job Card Automation System password", body)


def send_password_changed_notification(
    to_email: str | None,
    full_name: str,
    when_utc: str,
    actor: str = "you",
) -> bool:
    """Confirmation email sent after a successful password change.

    Doubles as a tamper alert: if the user didn't trigger this, they know to
    contact the garage admin immediately.
    """
    body = f"""Hello {full_name},

Your Job Card Automation System password was changed on {when_utc} UTC by {actor}.

All existing sign-in sessions have been signed out as a safety measure;
please sign in again with your new password.

If you did not make this change, contact your garage administrator
immediately so the account can be locked.

Regards,
Job Card Automation System
"""
    return send_email(to_email, "Your Job Card Automation System password was changed", body)
