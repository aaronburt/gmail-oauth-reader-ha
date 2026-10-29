from typing import Final

DOMAIN: Final = "gmail_oauth_reader"

GMAIL_MESSAGES_URL: Final = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
GMAIL_SEND_URL: Final = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GMAIL_PROFILE_URL: Final = "https://gmail.googleapis.com/gmail/v1/users/me/profile"

SCOPE_GMAIL_READONLY: Final = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_GMAIL_MODIFY: Final = "https://www.googleapis.com/auth/gmail.modify"
SCOPE_GMAIL_SEND: Final = "https://www.googleapis.com/auth/gmail.send"

SCOPES_READONLY: Final = [
    SCOPE_GMAIL_READONLY,
]

SCOPES_WRITE: Final = [
    SCOPE_GMAIL_MODIFY,
]

SCOPES: Final = SCOPES_READONLY

CONF_QUERY: Final = "query"
DEFAULT_QUERY: Final = "is:unread label:INBOX"

CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_QUEUE_DWELL_TIME: Final = "queue_dwell_time"
CONF_EXTRACT_OTP: Final = "extract_otp"
CONF_OTP_EXPIRY_MINUTES: Final = "otp_expiry_minutes"
CONF_ENABLE_WRITE: Final = "enable_write"
DEFAULT_DOWNLOAD_DIR: Final = "www/gmail_attachments"

DEFAULT_POLL_INTERVAL: Final = 60
DEFAULT_QUEUE_DWELL_TIME: Final = 5
DEFAULT_EXTRACT_OTP: Final = True
DEFAULT_OTP_EXPIRY_MINUTES: Final = 15
DEFAULT_ENABLE_WRITE: Final = False

SERVICE_SEND_EMAIL: Final = "send_email"

MIN_POLL_INTERVAL: Final = 30
MAX_POLL_INTERVAL: Final = 600

MIN_QUEUE_DWELL_TIME: Final = 1
MAX_QUEUE_DWELL_TIME: Final = 60

MIN_OTP_EXPIRY_MINUTES: Final = 1
MAX_OTP_EXPIRY_MINUTES: Final = 60

STATE_IDLE: Final = "idle"

MAX_SEEN_CACHE_SIZE: Final = 500
LIST_PAGE_SIZE: Final = 20
MAX_LIST_PAGES: Final = 10
LATE_ARRIVAL_GRACE_MS: Final = 5 * 60 * 1000
MAX_DETAIL_FETCH_ATTEMPTS: Final = 3
MAX_BODY_PREVIEW_LENGTH: Final = 200
MAX_RECENT_EMAILS: Final = 20

EVENT_GMAIL_NEW_EMAIL: Final = "gmail_oauth_reader_new_email"
EVENT_GMAIL_NEW_OTP: Final = "gmail_oauth_reader_new_otp"
TRIGGER_TYPE_NEW_EMAIL: Final = "new_email"
TRIGGER_TYPE_NEW_OTP: Final = "new_otp"

SERVICE_SIMULATE_EMAIL: Final = "simulate_email"
SIMULATED_MESSAGE_PREFIX: Final = "sim_"
