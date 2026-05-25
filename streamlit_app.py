import os
from datetime import date, datetime
from pathlib import Path

import httpx
import streamlit as st


API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
APP_NAME = "Job Card Automation System"

KENYA_COUNTIES = [
    "In shop",
    "Baringo",
    "Bomet",
    "Bungoma",
    "Busia",
    "Elgeyo Marakwet",
    "Embu",
    "Garissa",
    "Homa Bay",
    "Isiolo",
    "Kajiado",
    "Kakamega",
    "Kericho",
    "Kiambu",
    "Kilifi",
    "Kirinyaga",
    "Kisii",
    "Kisumu",
    "Kitui",
    "Kwale",
    "Laikipia",
    "Lamu",
    "Machakos",
    "Makueni",
    "Mandera",
    "Marsabit",
    "Meru",
    "Migori",
    "Mombasa",
    "Murang'a",
    "Nairobi",
    "Nakuru",
    "Nandi",
    "Narok",
    "Nyamira",
    "Nyandarua",
    "Nyeri",
    "Samburu",
    "Siaya",
    "Taita Taveta",
    "Tana River",
    "Tharaka Nithi",
    "Trans Nzoia",
    "Turkana",
    "Uasin Gishu",
    "Vihiga",
    "Wajir",
    "West Pokot",
]

CAR_MODELS = [
    "Toyota",
    "Mazda",
    "Nissan",
    "Subaru",
    "Mitsubishi",
    "Isuzu",
    "Mercedes-Benz",
    "BMW",
    "Volkswagen",
    "Honda",
    "Suzuki",
    "Hyundai",
    "Kia",
    "Ford",
    "Other",
]

SERVICE_OPTIONS = {
    "In shop": "in_shop",
    "Pickup": "pickup",
    "Drop off": "dropoff",
    "Pickup and drop off": "pickup_and_dropoff",
}

JOB_CARD_STATUS_OPTIONS = ["draft", "in_progress", "ready", "invoiced", "complete", "closed"]


st.set_page_config(page_title=APP_NAME, layout="wide")

st.markdown(
    """
    <style>
    /* Everything in here uses Streamlit theme variables
       (--background-color, --secondary-background-color, --text-color,
       --primary-color) or semi-transparent overlays so the styling adapts
       to both light and dark themes without us hardcoding hex values that
       only look right under one of them. */

    .block-container {padding-top: 1.2rem; padding-bottom: 2.5rem; max-width: 1280px;}

    /* Metric cards — surface from the theme, soft border via opacity. */
    div[data-testid="stMetric"] {
        background: var(--secondary-background-color);
        border: 1px solid rgba(127, 127, 127, 0.22);
        padding: 14px 16px;
        border-radius: 10px;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
    }
    div[data-testid="stMetric"] label {opacity: 0.75; font-weight: 500;}

    /* Tabs — easier to scan; selected state uses a translucent accent so
       it reads in both themes. */
    .stTabs [data-baseweb="tab-list"] {gap: 4px;}
    .stTabs [data-baseweb="tab"] {height: 42px; padding: 0 18px; border-radius: 8px;}
    .stTabs [aria-selected="true"] {background: rgba(59, 130, 246, 0.18);}

    /* Dataframe headers — bold without forcing a background colour that
       would clash with the dark theme. */
    div[data-testid="stDataFrame"] [role="columnheader"] {font-weight: 600;}

    /* Subheader rhythm. */
    .block-container h4 {margin-top: 0.75rem; margin-bottom: 0.4rem;}
    .block-container h3 {margin-top: 1.0rem;}

    /* Buttons feel a bit more tactile. */
    div[data-testid="stButton"] button {transition: transform 0.05s ease, box-shadow 0.05s ease;}
    div[data-testid="stButton"] button:active {transform: translateY(1px);}

    /* Sidebar — let Streamlit set the background per theme, we just nudge
       the divider line and font-weight inside. */
    section[data-testid="stSidebar"] {border-right: 1px solid rgba(127, 127, 127, 0.22);}
    section[data-testid="stSidebar"] .stRadio label p {font-weight: 500;}
    </style>
    """,
    unsafe_allow_html=True,
)


def api_headers() -> dict:
    token = st.session_state.get("token")
    return {"Authorization": f"Bearer {token}"} if token else {}


@st.cache_data(ttl=60, show_spinner=False)
def cached_get(api_base_url: str, path: str, token: str | None, data_version: int):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(timeout=8) as client:
        response = client.get(f"{api_base_url}{path}", headers=headers)
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    return response.status_code, payload


# Mutating one resource sometimes invalidates the cached view of others —
# editing a job-card line, for instance, changes the inventory page. Keeping
# this map explicit beats the previous "wipe everything on every mutation"
# behaviour, which sent unrelated dropdowns through a round trip every time
# the admin saved anything.
_RESOURCE_INVALIDATION: dict[str, tuple[str, ...]] = {
    "job-cards": ("job-cards", "invoices", "inventory", "stock-movements"),
    "invoices": ("invoices", "job-cards"),
    "inventory": ("inventory", "garage-items", "stock-movements"),
    "stock-movements": ("stock-movements", "inventory"),
    "customer-vehicles": (
        "customer-vehicles",
        "customers",
        "vehicle-ownership-history",
    ),
    "customers": ("customers", "customer-vehicles"),
}


def _path_resource(path: str) -> str:
    parts = path.strip("/").split("/", 1)
    return parts[0] if parts and parts[0] else "__root__"


def _data_version_for(resource: str) -> int:
    versions = st.session_state.setdefault("data_versions", {})
    return versions.get(resource, 0)


def bump_data_version(path: str | None = None) -> None:
    """Invalidate the cached view for the resource family touched by ``path``
    plus any cross-cutting families. Passing ``None`` invalidates every known
    family (used on login / logout)."""
    versions = st.session_state.setdefault("data_versions", {})
    if path is None:
        for key in list(versions.keys()):
            versions[key] = versions[key] + 1
        return
    family = _path_resource(path)
    targets = _RESOURCE_INVALIDATION.get(family, (family,))
    for resource in targets:
        versions[resource] = versions.get(resource, 0) + 1


def api_request(method: str, path: str, json: dict | None = None):
    try:
        if method.upper() == "GET":
            status_code, payload = cached_get(
                API_BASE_URL,
                path,
                st.session_state.get("token"),
                _data_version_for(_path_resource(path)),
            )
            if status_code >= 400:
                detail = (
                    payload.get("detail", payload)
                    if isinstance(payload, dict)
                    else payload
                )
                st.error(detail)
                return None
            return payload

        with httpx.Client(timeout=8) as client:
            response = client.request(
                method,
                f"{API_BASE_URL}{path}",
                headers=api_headers(),
                json=clean_payload(json) if json else None,
            )
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            st.error(detail)
            return None
        bump_data_version(path)
        return response.json()
    except httpx.RequestError:
        st.error("The API is not reachable. Start FastAPI first, then refresh this page.")
        return None


@st.cache_data(ttl=600, show_spinner=False)
def _cached_invoice_pdf(
    api_base_url: str, invoice_id: int, token: str | None, version: int
):
    """PDF generation is expensive — cache per invoice id + invoice-cache
    version so the download button doesn't trigger a fresh render every time
    Streamlit re-runs the script."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(timeout=15) as client:
        response = client.get(
            f"{api_base_url}/invoices/{invoice_id}/pdf", headers=headers
        )
    if response.status_code >= 400:
        return response.status_code, None
    return response.status_code, response.content


def cached_invoice_pdf(invoice_id: int) -> bytes | None:
    status_code, content = _cached_invoice_pdf(
        API_BASE_URL,
        invoice_id,
        st.session_state.get("token"),
        _data_version_for("invoices"),
    )
    return content if status_code < 400 else None


def api_binary_get(path: str):
    try:
        with httpx.Client(timeout=15) as client:
            response = client.get(f"{API_BASE_URL}{path}", headers=api_headers())
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            st.error(detail)
            return None
        return response.content
    except httpx.RequestError:
        st.error("The API is not reachable. Start FastAPI first, then refresh this page.")
        return None


def clean_value(value):
    if isinstance(value, dict):
        return {
            key: cleaned
            for key, item in value.items()
            if (cleaned := clean_value(item)) is not None
        }
    if isinstance(value, list):
        return [cleaned for item in value if (cleaned := clean_value(item)) is not None]
    if value in ("", None):
        return None
    return value


def clean_payload(payload: dict) -> dict:
    return {
        key: cleaned
        for key, value in payload.items()
        if (cleaned := clean_value(value)) is not None
    }


def save_uploaded_file(uploaded_file, folder: str) -> str | None:
    if not uploaded_file:
        return None
    target_dir = Path(folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(
        char if char.isalnum() or char in ("-", "_", ".") else "_"
        for char in uploaded_file.name
    )
    path_name = Path(safe_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = target_dir / f"{path_name.stem}_{timestamp}{path_name.suffix}"
    target.write_bytes(uploaded_file.getbuffer())
    return str(target.resolve())


def remember_login(result: dict) -> None:
    st.session_state.token = result["access_token"]
    st.session_state.role = result["role"]
    st.session_state.full_name = result["full_name"]
    st.session_state.is_bootstrap_admin = result.get("is_bootstrap_admin", False)
    st.query_params["token"] = result["access_token"]


def restore_login_from_url() -> None:
    if st.session_state.get("token"):
        return
    token = st.query_params.get("token")
    if not token:
        return

    try:
        with httpx.Client(timeout=8) as client:
            response = client.get(
                f"{API_BASE_URL}/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.RequestError:
        return

    if response.status_code != 200:
        st.query_params.clear()
        return

    user = response.json()
    st.session_state.token = token
    st.session_state.role = user["role"]
    st.session_state.full_name = user["full_name"]
    st.session_state.is_bootstrap_admin = False


def is_admin() -> bool:
    return st.session_state.get("role") == "admin"


def require_admin_notice() -> bool:
    if is_admin():
        return True
    st.info("You are signed in with read-only access.")
    return False


def active_only(records: list[dict]) -> list[dict]:
    return [record for record in records if record.get("is_active", True)]


def render_reset_password_screen(reset_token: str) -> None:
    st.title("Reset password")
    st.caption("Choose a new password for your account.")
    with st.form("reset_password_form"):
        password = st.text_input("New password", type="password")
        confirm_password = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Update password", width="stretch")
    if submitted:
        if password != confirm_password:
            st.error("The passwords do not match.")
            return
        result = api_request(
            "POST",
            "/auth/reset-password",
            {"token": reset_token, "password": password},
        )
        if result:
            st.success(result.get("message", "Password updated. You can now sign in."))
            st.query_params.clear()
            st.rerun()


def _render_sign_in_form(needs_bootstrap: bool) -> None:
    with st.form("login_form", clear_on_submit=False):
        full_name = ""
        if needs_bootstrap:
            full_name = st.text_input("Full name")
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", width="stretch", type="primary")

    if submitted:
        payload = {"email": email, "password": password}
        if needs_bootstrap:
            payload["full_name"] = full_name
        result = api_request("POST", "/auth/login", payload)
        if result:
            # Clear the failed-sign-in flag so the reset link disappears next render.
            st.session_state.pop("show_password_reset_link", None)
            remember_login(result)
            st.rerun()
        elif not needs_bootstrap:
            # api_request already surfaced the error. Flag the failure so the
            # next render reveals a small "Reset password" link — the only
            # time the option appears on the login screen.
            st.session_state.show_password_reset_link = True

    # Default state: just the sign-in form. The reset link is hidden until a
    # failed attempt makes it useful, keeping the login UI clean.
    if (
        not needs_bootstrap
        and st.session_state.get("show_password_reset_link")
    ):
        st.caption("Trouble signing in?")
        if st.button("Reset your password", key="show_forgot_password_link"):
            st.session_state.login_mode = "forgot_password"
            st.rerun()


def _render_forgot_password_form() -> None:
    st.subheader("Reset your password")
    st.caption(
        "Enter your account email. If the address is on file we'll send a "
        "one-time link that lets you choose a new password."
    )
    with st.form("forgot_password_form", clear_on_submit=False):
        reset_email = st.text_input("Account email", key="forgot_password_email")
        submitted = st.form_submit_button("Send reset link", width="stretch", type="primary")
    if submitted:
        result = api_request("POST", "/auth/forgot-password", {"email": reset_email})
        if result:
            st.success(
                result.get("message")
                or "If that email exists, a password reset link has been sent."
            )

    if st.button("← Back to sign in", key="back_to_sign_in", width="stretch"):
        st.session_state.login_mode = "sign_in"
        st.rerun()


def login_screen() -> None:
    reset_token = st.query_params.get("reset_token")
    if reset_token:
        render_reset_password_screen(reset_token)
        return

    # Centre the login form in a narrow column for a cleaner hero feel.
    spacer_left, content, spacer_right = st.columns([1, 2, 1])
    with content:
        st.title(APP_NAME)
        st.caption("Sign in to manage job cards, inventory, and invoices.")

        status = api_request("GET", "/auth/bootstrap-status")
        needs_bootstrap = bool(status and status.get("needs_bootstrap"))

        if needs_bootstrap:
            st.success(
                "No users exist yet. This first sign-in will create the "
                "administrator account."
            )

        mode = st.session_state.get("login_mode", "sign_in")
        if mode == "forgot_password" and not needs_bootstrap:
            _render_forgot_password_form()
        else:
            _render_sign_in_form(needs_bootstrap)


_CURRENCY_COLUMN_HINTS = {
    "amount",
    "balance",
    "cost",
    "default_amount",
    "default_sale_price",
    "labour_amount",
    "price",
    "rate",
    "sale_price",
    "subtotal",
    "total",
    "total_amount",
    "unit_cost",
    "unit_price",
    "vat_amount",
    "amount_paid",
    "discount_amount",
    "balance_due",
    "latest_unit_cost",
    "latest_sale_price",
    "invoice_total",
}

_INTEGER_COLUMN_HINTS = {"quantity", "quantity_on_hand", "year", "remaining_quantity"}


def _prettify_column(name: str) -> str:
    """`snake_case` → `Snake Case` — friendlier in table headers."""
    return name.replace("_", " ").strip().title()


def _column_config_for(rows: list[dict]) -> dict | None:
    """Build a column_config that formats obvious money / date columns and
    prettifies all header names. Returns None if there's nothing to render."""
    if not rows:
        return None
    config: dict = {}
    sample = rows[0]
    for key in sample.keys():
        nice = _prettify_column(key)
        lower = key.lower()
        if lower in _CURRENCY_COLUMN_HINTS or lower.endswith("_amount"):
            config[key] = st.column_config.NumberColumn(nice, format="KES %,.2f")
        elif lower in _INTEGER_COLUMN_HINTS:
            config[key] = st.column_config.NumberColumn(nice, format="%,.2f")
        elif lower in {"created_at", "updated_at", "expires_at"}:
            config[key] = st.column_config.DatetimeColumn(nice, format="YYYY-MM-DD HH:mm")
        else:
            config[key] = st.column_config.Column(nice)
    return config


def display_records(
    records: list[dict],
    key: str,
    page_size: int = 10,
    empty_message: str = "Nothing here yet.",
) -> list[dict]:
    if not records:
        st.info(empty_message)
        return []

    controls = st.columns([3, 1, 1]) if any("is_active" in r for r in records) else st.columns([3, 1])
    search = controls[0].text_input(
        "Search", key=f"{key}_search", placeholder="Type to filter…",
        label_visibility="collapsed",
    )
    filtered = records
    if search:
        needle = search.lower()
        filtered = [
            record
            for record in records
            if needle in " ".join(str(value).lower() for value in record.values()).lower()
        ]
    if any("is_active" in record for record in records):
        status_filter = controls[1].selectbox(
            "Status", ["Active", "All", "Inactive"],
            key=f"{key}_status", label_visibility="collapsed",
        )
        if status_filter == "Active":
            filtered = [record for record in filtered if record.get("is_active", True)]
        elif status_filter == "Inactive":
            filtered = [record for record in filtered if not record.get("is_active", True)]

    total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
    page = controls[-1].number_input(
        "Page", min_value=1, max_value=total_pages,
        value=1, step=1, key=f"{key}_page",
        label_visibility="collapsed",
    )
    start = (page - 1) * page_size
    visible = filtered[start : start + page_size]
    st.caption(
        f"Showing {len(visible)} of {len(filtered)} record(s) · page {page} of {total_pages}"
    )

    rows = [
        {k: v for k, v in record.items() if k != "id" and not k.endswith("_id")}
        for record in visible
    ]
    column_config = _column_config_for(rows)
    st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        column_config=column_config,
    )
    return filtered


def select_record(records: list[dict], label: str, key: str, default_id: int | None = None) -> dict | None:
    if not records:
        return None
    options = {}
    label_counts = {}
    for record in records:
        base_label = (
            record.get("name")
            or record.get("full_name")
            or record.get("registration")
            or record.get("invoice_number")
            or record.get("job_number")
            or record.get("item_name")
        )
        base_label = str(base_label or "Unnamed")
        detail = record.get("email") or record.get("phone") or record.get("car_registration")
        option_label = f"{base_label} - {detail}" if detail else base_label
        label_counts[option_label] = label_counts.get(option_label, 0) + 1
        if label_counts[option_label] > 1:
            option_label = f"{option_label} ({label_counts[option_label]})"
        options[option_label] = record
    option_labels = list(options.keys())
    selected_index = 0
    if default_id is not None:
        for index, option_label in enumerate(option_labels):
            if options[option_label].get("id") == default_id:
                selected_index = index
                break
    selected = st.selectbox(label, option_labels, index=selected_index, key=key)
    return options[selected]


def select_option(records: list[dict], label: str, key: str, name_field: str = "name") -> dict | None:
    if not records:
        return None
    options = {}
    label_counts = {}
    for record in records:
        base_label = record.get(name_field) or record.get("full_name") or record.get("item_name") or "Unnamed"
        option_label = str(base_label)
        detail = record.get("latest_supplier") or record.get("phone") or record.get("email")
        if detail:
            option_label = f"{option_label} - {detail}"
        label_counts[option_label] = label_counts.get(option_label, 0) + 1
        if label_counts[option_label] > 1:
            option_label = f"{option_label} ({label_counts[option_label]})"
        options[option_label] = record
    selected = st.selectbox(label, list(options.keys()), key=key)
    return options[selected]


def render_suppliers() -> None:
    records = api_request("GET", "/suppliers") or []
    records = display_records(records, "suppliers")
    if not require_admin_notice():
        return

    create_col, edit_col = st.columns(2)
    with create_col:
        st.subheader("Add supplier")
        with st.form("supplier_create"):
            payload = {
                "name": st.text_input("Supplier name"),
                "contact_person": st.text_input("Contact person"),
                "phone": st.text_input("Phone"),
                "email": st.text_input("Email"),
                "notes": st.text_area("Notes"),
                "is_active": st.checkbox("Active", value=True),
            }
            if st.form_submit_button("Save supplier", width="stretch"):
                if api_request("POST", "/suppliers", payload):
                    st.rerun()

    with edit_col:
        st.subheader("Edit supplier")
        selected = select_record(records, "Supplier", "supplier_edit_select")
        if selected:
            with st.form(f"supplier_edit_{selected['id']}"):
                payload = {
                    "name": st.text_input("Supplier name", value=selected.get("name", "")),
                    "contact_person": st.text_input(
                        "Contact person", value=selected.get("contact_person") or ""
                    ),
                    "phone": st.text_input("Phone", value=selected.get("phone") or ""),
                    "email": st.text_input("Email", value=selected.get("email") or ""),
                    "notes": st.text_area("Notes", value=selected.get("notes") or ""),
                    "is_active": st.checkbox("Active", value=selected.get("is_active", True)),
                }
                save, delete = st.columns(2)
                if save.form_submit_button("Update", width="stretch"):
                    if api_request("PATCH", f"/suppliers/{selected['id']}", payload):
                        st.rerun()
                if delete.form_submit_button("Deactivate", width="stretch"):
                    if api_request("DELETE", f"/suppliers/{selected['id']}"):
                        st.rerun()


def render_users() -> None:
    records = api_request("GET", "/users") or []
    records = display_records(records, "users")
    if not require_admin_notice():
        return

    create_col, edit_col = st.columns(2)
    with create_col:
        st.subheader("Add user")
        with st.form("user_create"):
            payload = {
                "full_name": st.text_input("Full name"),
                "email": st.text_input("Email"),
                "password": st.text_input("Temporary password", type="password"),
                "role": st.selectbox("Role", ["user", "admin"]),
                "is_active": st.checkbox("Active", value=True),
            }
            if st.form_submit_button("Save user", width="stretch"):
                if api_request("POST", "/users", payload):
                    st.rerun()

    with edit_col:
        st.subheader("Edit user")
        selected = select_record(records, "User", "user_edit_select")
        if selected:
            with st.form(f"user_edit_{selected['id']}"):
                payload = {
                    "full_name": st.text_input("Full name", value=selected.get("full_name", "")),
                    "password": st.text_input("New password", type="password"),
                    "role": st.selectbox("Role", ["user", "admin"], index=0 if selected["role"] == "user" else 1),
                    "is_active": st.checkbox("Active", value=selected.get("is_active", True)),
                }
                save, delete = st.columns(2)
                if save.form_submit_button("Update", width="stretch"):
                    if api_request("PATCH", f"/users/{selected['id']}", payload):
                        st.rerun()
                if delete.form_submit_button("Deactivate", width="stretch"):
                    if api_request("DELETE", f"/users/{selected['id']}"):
                        st.rerun()


def render_workers() -> None:
    records = api_request("GET", "/workers") or []
    records = display_records(records, "workers")
    if not require_admin_notice():
        return

    create_col, edit_col = st.columns(2)
    with create_col:
        st.subheader("Add worker")
        with st.form("worker_create"):
            payload = {
                "full_name": st.text_input("Full name"),
                "phone": st.text_input("Phone"),
                "email": st.text_input("Email"),
                "skill": st.text_input("Main skill"),
                "is_active": st.checkbox("Active", value=True),
            }
            if st.form_submit_button("Save worker", width="stretch"):
                if api_request("POST", "/workers", payload):
                    st.rerun()

    with edit_col:
        st.subheader("Edit worker")
        selected = select_record(records, "Worker", "worker_edit_select")
        if selected:
            with st.form(f"worker_edit_{selected['id']}"):
                payload = {
                    "full_name": st.text_input("Full name", value=selected.get("full_name", "")),
                    "phone": st.text_input("Phone", value=selected.get("phone") or ""),
                    "email": st.text_input("Email", value=selected.get("email") or ""),
                    "skill": st.text_input("Main skill", value=selected.get("skill") or ""),
                    "is_active": st.checkbox("Active", value=selected.get("is_active", True)),
                }
                save, delete = st.columns(2)
                if save.form_submit_button("Update", width="stretch"):
                    if api_request("PATCH", f"/workers/{selected['id']}", payload):
                        st.rerun()
                if delete.form_submit_button("Deactivate", width="stretch"):
                    if api_request("DELETE", f"/workers/{selected['id']}"):
                        st.rerun()


def customer_payload(prefix: str, selected: dict | None = None) -> dict:
    selected = selected or {}
    make = selected.get("car_make", CAR_MODELS[0])
    model_index = CAR_MODELS.index(make) if make in CAR_MODELS else CAR_MODELS.index("Other")
    service_value = selected.get("service_option", "in_shop")
    service_labels = list(SERVICE_OPTIONS.keys())
    service_index = list(SERVICE_OPTIONS.values()).index(service_value)

    full_name = st.text_input("Customer name", value=selected.get("full_name", ""), key=f"{prefix}_name")
    phone = st.text_input("WhatsApp number", value=selected.get("phone", ""), key=f"{prefix}_phone")
    email = st.text_input("Email", value=selected.get("email") or "", key=f"{prefix}_email")
    car_registration = st.text_input(
        "Car registration", value=selected.get("car_registration", ""), key=f"{prefix}_reg"
    )
    chosen_make = st.selectbox("Car make", CAR_MODELS, index=model_index, key=f"{prefix}_make")
    other_make = st.text_input(
        "Other make",
        value=make if make not in CAR_MODELS else "",
        key=f"{prefix}_other_make",
        help="Fill this when Car make is Other.",
    )
    car_type = st.text_input("Car type", value=selected.get("car_type") or "", key=f"{prefix}_type")
    current_year = date.today().year
    car_year = st.number_input(
        "Year",
        min_value=1950,
        max_value=current_year + 1,
        value=selected.get("car_year") or current_year,
        key=f"{prefix}_year",
    )
    county = st.selectbox(
        "Location",
        KENYA_COUNTIES,
        index=KENYA_COUNTIES.index(selected.get("county", "In shop"))
        if selected.get("county", "In shop") in KENYA_COUNTIES
        else 0,
        key=f"{prefix}_county",
    )
    service_label = st.selectbox(
        "Service option", service_labels, index=service_index, key=f"{prefix}_service"
    )
    location_description = st.text_area(
        "Location description",
        value=selected.get("location_description") or "",
        key=f"{prefix}_loc_desc",
    )
    car_description = st.text_area(
        "Car description", value=selected.get("car_description") or "", key=f"{prefix}_car_desc"
    )

    return {
        "full_name": full_name,
        "phone": phone,
        "email": email,
        "car_registration": car_registration.upper(),
        "car_make": other_make if chosen_make == "Other" else chosen_make,
        "car_type": car_type,
        "car_year": int(car_year),
        "county": county,
        "location_description": location_description,
        "service_option": SERVICE_OPTIONS[service_label],
        "car_description": car_description,
    }


def vehicle_payload(prefix: str, customer_id: int, selected: dict | None = None) -> dict:
    selected = selected or {}
    make = selected.get("make", CAR_MODELS[0])
    make_index = CAR_MODELS.index(make) if make in CAR_MODELS else CAR_MODELS.index("Other")

    chosen_make = st.selectbox("Car make", CAR_MODELS, index=make_index, key=f"{prefix}_make")
    other_make = st.text_input(
        "Other make",
        value=make if make not in CAR_MODELS else "",
        key=f"{prefix}_other_make",
        help="Fill this when Car make is Other.",
    )
    current_year = date.today().year
    return {
        "customer_id": customer_id,
        "registration": st.text_input(
            "Registration",
            value=selected.get("registration", ""),
            key=f"{prefix}_registration",
        ).upper(),
        "make": other_make if chosen_make == "Other" else chosen_make,
        "vehicle_type": st.text_input(
            "Car type",
            value=selected.get("vehicle_type") or "",
            key=f"{prefix}_vehicle_type",
        ),
        "year": int(
            st.number_input(
                "Year",
                min_value=1950,
                max_value=current_year + 1,
                value=selected.get("year") or current_year,
                key=f"{prefix}_year",
            )
        ),
        "description": st.text_area(
            "Description",
            value=selected.get("description") or "",
            key=f"{prefix}_description",
        ),
        "is_active": st.checkbox(
            "Active",
            value=selected.get("is_active", True),
            key=f"{prefix}_is_active",
        ),
    }


def render_customers() -> None:
    records = api_request("GET", "/customers") or []
    records = display_records(records, "customers")
    if not require_admin_notice():
        return

    add_col, file_col = st.columns([1, 2])
    with add_col:
        st.subheader("Add customer")
        with st.form("customer_create"):
            payload = customer_payload("create_customer")
            if st.form_submit_button("Save customer", width="stretch"):
                if api_request("POST", "/customers", payload):
                    st.rerun()

    with file_col:
        st.subheader("Customer file")
        selected = select_record(records, "Open customer", "customer_file_select")
        if not selected:
            return

        detail_cols = st.columns(4)
        detail_cols[0].metric("Name", selected.get("full_name") or "")
        detail_cols[1].metric("Phone", selected.get("phone") or "")
        detail_cols[2].metric("County", selected.get("county") or "")
        detail_cols[3].metric("Status", "Active" if selected.get("is_active", True) else "Inactive")
        st.caption(f"Email: {selected.get('email') or 'Not provided'}")
        st.caption(f"Location: {selected.get('location_description') or 'Not provided'}")

        vehicles = api_request("GET", f"/customer-vehicles?customer_id={selected['id']}") or []
        st.subheader("Cars owned by this customer")
        display_records(vehicles, f"customer_{selected['id']}_vehicles", page_size=5)

        action = st.radio(
            "Action",
            ["Add car", "Edit customer", "Edit car", "Transfer car"],
            horizontal=True,
            key=f"customer_action_{selected['id']}",
        )

        if action == "Add car":
            with st.form(f"vehicle_create_{selected['id']}"):
                payload = vehicle_payload(f"vehicle_create_{selected['id']}", selected["id"])
                if st.form_submit_button("Save car", width="stretch"):
                    if api_request("POST", "/customer-vehicles", payload):
                        st.rerun()

        elif action == "Edit customer":
            with st.form(f"customer_edit_{selected['id']}"):
                payload = customer_payload(f"edit_customer_{selected['id']}", selected)
                save, delete = st.columns(2)
                if save.form_submit_button("Update", width="stretch"):
                    if api_request("PATCH", f"/customers/{selected['id']}", payload):
                        st.rerun()
                if delete.form_submit_button("Deactivate", width="stretch"):
                    if api_request("DELETE", f"/customers/{selected['id']}"):
                        st.rerun()

        elif action == "Edit car":
            selected_vehicle = select_record(vehicles, "Car to edit", f"vehicle_edit_select_{selected['id']}")
            if selected_vehicle:
                with st.form(f"vehicle_edit_{selected_vehicle['id']}"):
                    payload = vehicle_payload(
                        f"vehicle_edit_{selected_vehicle['id']}",
                        selected["id"],
                        selected_vehicle,
                    )
                    save, deactivate = st.columns(2)
                    if save.form_submit_button("Update car", width="stretch"):
                        if api_request("PATCH", f"/customer-vehicles/{selected_vehicle['id']}", payload):
                            st.rerun()
                    if deactivate.form_submit_button("Deactivate car", width="stretch"):
                        if api_request("DELETE", f"/customer-vehicles/{selected_vehicle['id']}"):
                            st.rerun()

        elif action == "Transfer car":
            selected_vehicle = select_record(
                vehicles,
                "Car to transfer",
                f"vehicle_transfer_select_{selected['id']}",
            )
            owner_options = [customer for customer in records if customer["id"] != selected["id"]]
            new_owner = select_record(owner_options, "New owner", f"vehicle_new_owner_select_{selected['id']}")
            if selected_vehicle and new_owner:
                st.info(
                    f"Transfer {selected_vehicle.get('registration')} from "
                    f"{selected.get('full_name')} to {new_owner.get('full_name')}."
                )
                transfer_notes = st.text_area(
                    "Transfer notes",
                    key=f"vehicle_transfer_notes_{selected_vehicle['id']}",
                )
                if st.button("Transfer car", width="stretch", key=f"transfer_{selected_vehicle['id']}"):
                    payload = {"new_customer_id": new_owner["id"], "notes": transfer_notes}
                    if api_request("POST", f"/customer-vehicles/{selected_vehicle['id']}/transfer", payload):
                        st.success("Vehicle ownership updated.")
                        st.rerun()
                history = api_request(
                    "GET",
                    f"/vehicle-ownership-history?vehicle_id={selected_vehicle['id']}",
                ) or []
                if history:
                    customers_by_id = {customer["id"]: customer.get("full_name") for customer in records}
                    history_rows = [
                        {
                            "from": customers_by_id.get(item["previous_customer_id"], "Unknown"),
                            "to": customers_by_id.get(item["new_customer_id"], "Unknown"),
                            "notes": item.get("notes"),
                        }
                        for item in history
                    ]
                    st.caption("Ownership history")
                    st.dataframe(history_rows, hide_index=True, width="stretch")


def render_named_records(title: str, path: str, description_label: str = "Description") -> None:
    records = api_request("GET", path) or []
    records = display_records(records, path.strip("/").replace("-", "_"))
    if not require_admin_notice():
        return

    create_col, edit_col = st.columns(2)
    with create_col:
        st.subheader(f"Add {title.lower()}")
        with st.form(f"{path}_create"):
            payload = {
                "name": st.text_input("Name"),
                "description": st.text_area(description_label),
                "is_active": st.checkbox("Active", value=True),
            }
            if path == "/garage-costs":
                payload["default_amount"] = st.number_input("Default amount", min_value=0.0, step=100.0)
            if st.form_submit_button("Save", width="stretch"):
                if api_request("POST", path, payload):
                    st.rerun()

    with edit_col:
        st.subheader(f"Edit {title.lower()}")
        selected = select_record(records, title, f"{path}_edit_select")
        if selected:
            with st.form(f"{path}_edit_{selected['id']}"):
                payload = {
                    "name": st.text_input("Name", value=selected.get("name", "")),
                    "description": st.text_area(
                        description_label, value=selected.get("description") or ""
                    ),
                    "is_active": st.checkbox("Active", value=selected.get("is_active", True)),
                }
                if path == "/garage-costs":
                    amount = selected.get("default_amount") or 0
                    payload["default_amount"] = st.number_input(
                        "Default amount", min_value=0.0, value=float(amount), step=100.0
                    )
                save, delete = st.columns(2)
                if save.form_submit_button("Update", width="stretch"):
                    if api_request("PATCH", f"{path}/{selected['id']}", payload):
                        st.rerun()
                if delete.form_submit_button("Deactivate", width="stretch"):
                    if api_request("DELETE", f"{path}/{selected['id']}"):
                        st.rerun()


def render_garage_items() -> None:
    records = api_request("GET", "/garage-items") or []
    suppliers = api_request("GET", "/suppliers") or []
    supplier_options = {"None": None}
    supplier_options.update({supplier["name"]: supplier["id"] for supplier in suppliers})
    records = display_records(records, "garage_items")
    if not require_admin_notice():
        return

    create_col, edit_col = st.columns(2)
    with create_col:
        st.subheader("Add garage item")
        with st.form("garage_item_create"):
            supplier_name = st.selectbox("Default supplier", list(supplier_options.keys()))
            payload = {
                "name": st.text_input("Item name"),
                "category": st.text_input("Category"),
                "unit": st.text_input("Unit"),
                "default_supplier_id": supplier_options[supplier_name],
                "description": st.text_area("Description"),
                "is_active": st.checkbox("Active", value=True),
            }
            if st.form_submit_button("Save item", width="stretch"):
                if api_request("POST", "/garage-items", payload):
                    st.rerun()

    with edit_col:
        st.subheader("Edit garage item")
        selected = select_record(records, "Garage item", "garage_item_edit_select")
        if selected:
            current_supplier = "None"
            for name, supplier_id in supplier_options.items():
                if supplier_id == selected.get("default_supplier_id"):
                    current_supplier = name
            with st.form(f"garage_item_edit_{selected['id']}"):
                supplier_name = st.selectbox(
                    "Default supplier",
                    list(supplier_options.keys()),
                    index=list(supplier_options.keys()).index(current_supplier),
                )
                payload = {
                    "name": st.text_input("Item name", value=selected.get("name", "")),
                    "category": st.text_input("Category", value=selected.get("category") or ""),
                    "unit": st.text_input("Unit", value=selected.get("unit") or ""),
                    "default_supplier_id": supplier_options[supplier_name],
                    "description": st.text_area("Description", value=selected.get("description") or ""),
                    "is_active": st.checkbox("Active", value=selected.get("is_active", True)),
                }
                save, delete = st.columns(2)
                if save.form_submit_button("Update", width="stretch"):
                    if api_request("PATCH", f"/garage-items/{selected['id']}", payload):
                        st.rerun()
                if delete.form_submit_button("Deactivate", width="stretch"):
                    if api_request("DELETE", f"/garage-items/{selected['id']}"):
                        st.rerun()


def render_email_settings() -> None:
    if not require_admin_notice():
        return

    status = api_request("GET", "/email/status")
    if not status:
        return

    cols = st.columns(4)
    cols[0].metric("SMTP enabled", "Yes" if status["enabled"] else "No")
    cols[1].metric("Ready", "Yes" if status["configured"] else "No")
    cols[2].metric("Host", status["host"])
    cols[3].metric("Port", status["port"])

    st.write(f"From email: {status['from_email'] or 'Not set'}")
    if not status["configured"]:
        st.warning("SMTP is not ready. Check SMTP_ENABLED, SMTP_USERNAME, SMTP_PASSWORD, and SMTP_FROM_EMAIL in .env, then restart the API.")

    with st.form("send_test_email"):
        to_email = st.text_input("Send test email to")
        submitted = st.form_submit_button("Send test email", width="stretch")
    if submitted:
        result = api_request("POST", "/email/test", {"to_email": to_email})
        if result:
            st.success("Test email sent.")

    if st.button("Resend my welcome email", width="stretch"):
        result = api_request("POST", "/email/resend-my-welcome")
        if result:
            st.success("Welcome email sent.")


def render_inventory() -> None:
    st.title("Inventory")
    stock = api_request("GET", "/inventory/stock") or []
    display_records(stock, "inventory_stock")
    if not require_admin_notice():
        return

    items = api_request("GET", "/garage-items") or []
    suppliers = api_request("GET", "/suppliers") or []
    receipts = api_request("GET", "/inventory/receipts") or []
    active_items = active_only(items)
    active_suppliers = active_only(suppliers)

    receive_col, history_col = st.columns([1, 2])
    with receive_col:
        st.subheader("Receive stock")
        with st.form("receive_stock"):
            item = select_option(active_items, "Garage item", "stock_item_select")
            supplier = select_option(active_suppliers, "Supplier", "stock_supplier_select")
            quantity = st.number_input("Quantity", min_value=0.01, step=1.0, key="stock_qty")
            unit_cost = st.number_input("Supplier unit cost", min_value=0.0, step=100.0, key="stock_unit_cost")
            sale_price = st.number_input("Default sale price", min_value=0.0, step=100.0, key="stock_sale_price")
            ref = st.text_input("Supplier invoice/reference")
            uploaded_invoice = st.file_uploader(
                "Attach supplier invoice",
                type=["pdf", "png", "jpg", "jpeg"],
                key="stock_supplier_invoice_file",
            )
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Receive stock", width="stretch")
        if submitted and item and supplier:
            attachment_path = save_uploaded_file(
                uploaded_invoice,
                "attachments/supplier_invoices",
            )
            payload = {
                "garage_item_id": item["id"],
                "supplier_id": supplier["id"],
                "quantity": quantity,
                "unit_cost": unit_cost,
                "default_sale_price": sale_price,
                "supplier_invoice_ref": ref,
                "supplier_invoice_file_path": attachment_path,
                "notes": notes,
            }
            if api_request("POST", "/inventory/receipts", payload):
                st.success("Stock received.")
                st.rerun()

    with history_col:
        st.subheader("Recent receipts")
        item_search = st.text_input("Find by item name", key="receipt_item_filter")
        supplier_names = sorted(
            {receipt.get("supplier_name") for receipt in receipts if receipt.get("supplier_name")}
        )
        supplier_filter = st.selectbox(
            "Filter by supplier",
            ["All suppliers", *supplier_names],
            key="receipt_supplier_filter",
        )
        filtered_receipts = receipts
        if item_search:
            filtered_receipts = [
                receipt
                for receipt in filtered_receipts
                if item_search.lower() in (receipt.get("item_name") or "").lower()
            ]
        if supplier_filter != "All suppliers":
            filtered_receipts = [
                receipt
                for receipt in filtered_receipts
                if receipt.get("supplier_name") == supplier_filter
            ]
        display_records(filtered_receipts, "inventory_receipts", page_size=8)

    _render_correct_receipt_panel(receipts, suppliers)
    render_inventory_cost_history(receipts)


def _format_receipt_label(receipt: dict) -> str:
    return (
        f"#{receipt.get('id')} · {receipt.get('item_name') or 'Item'}"
        f" · qty {receipt.get('quantity')} · {receipt.get('supplier_name') or 'Supplier'}"
    )


def _render_correct_receipt_panel(receipts: list[dict], suppliers: list[dict]) -> None:
    """Admin-only: edit or delete a previously-logged inventory receipt.

    Every change goes through the audit endpoint so the trail is preserved.
    Quantity edits are blocked from dropping below what's already been used;
    deletes are blocked entirely if any of the stock has been consumed.
    """
    st.subheader("Correct a receipt")
    st.caption(
        "Admin-only. Every edit or deletion is logged with the actor, "
        "timestamp, and the reason you provide. Quantity cannot be reduced "
        "below the amount already used on job cards; if you need to "
        "withdraw consumed stock, issue a corrective receipt instead."
    )
    if not receipts:
        st.caption("No receipts to correct yet.")
        return

    active_receipts = [r for r in receipts if r.get("is_active", True)]
    if not active_receipts:
        st.caption("No active receipts to correct.")
        return

    target = select_record(
        active_receipts,
        label="Pick a receipt",
        key="correct_receipt_select",
    )
    if not target:
        return

    used = float(target.get("quantity") or 0) - float(target.get("remaining_quantity") or 0)
    cols = st.columns(4)
    cols[0].metric("Quantity", f"{float(target.get('quantity') or 0):g}")
    cols[1].metric("Used", f"{used:g}")
    cols[2].metric("Remaining", f"{float(target.get('remaining_quantity') or 0):g}")
    cols[3].metric("Unit cost", f"KES {float(target.get('unit_cost') or 0):,.2f}")

    edit_tab, delete_tab, audit_tab = st.tabs(["Edit", "Delete", "Audit log"])

    with edit_tab:
        with st.form(f"correct_receipt_edit_{target['id']}", clear_on_submit=False):
            supplier_options = [
                {"id": s["id"], "name": s["name"]} for s in suppliers
            ]
            current_supplier = next(
                (
                    s
                    for s in supplier_options
                    if s["id"] == target.get("supplier_id")
                ),
                None,
            )
            supplier_pick = select_option(
                supplier_options,
                "Supplier",
                key=f"correct_receipt_supplier_{target['id']}",
            )
            new_quantity = st.number_input(
                "Quantity",
                min_value=0.01,
                value=float(target.get("quantity") or 0),
                step=1.0,
                help=(
                    f"Cannot go below the {used:g} unit(s) already used on "
                    "job cards."
                ),
            )
            new_unit_cost = st.number_input(
                "Supplier unit cost",
                min_value=0.0,
                value=float(target.get("unit_cost") or 0),
                step=100.0,
            )
            new_sale_price = st.number_input(
                "Default sale price",
                min_value=0.0,
                value=float(target.get("default_sale_price") or 0),
                step=100.0,
            )
            new_ref = st.text_input(
                "Supplier invoice/reference",
                value=target.get("supplier_invoice_ref") or "",
            )
            new_notes = st.text_area("Notes", value=target.get("notes") or "")
            reason = st.text_area(
                "Reason for the change (required)",
                placeholder="e.g. supplier issued a credit note reducing the quantity",
            )
            save = st.form_submit_button("Save correction", width="stretch", type="primary")
        if save:
            if len(reason.strip()) < 3:
                st.error("A reason of at least 3 characters is required.")
                return
            payload = {
                "supplier_id": (supplier_pick or current_supplier or {}).get("id"),
                "quantity": new_quantity,
                "unit_cost": new_unit_cost,
                "default_sale_price": new_sale_price,
                "supplier_invoice_ref": new_ref,
                "notes": new_notes,
                "reason": reason.strip(),
            }
            if api_request("PATCH", f"/inventory/receipts/{target['id']}", payload):
                st.success("Receipt corrected. The change is in the audit log.")
                st.rerun()

    with delete_tab:
        if used > 0:
            st.warning(
                f"{used:g} unit(s) have already been used on job cards. "
                "Deletion is blocked — issue a corrective receipt instead."
            )
        with st.form(f"correct_receipt_delete_{target['id']}", clear_on_submit=False):
            reason = st.text_area(
                "Reason for deletion (required)",
                placeholder="e.g. logged in error — duplicate of receipt #42",
            )
            confirmed = st.checkbox(
                "I understand this soft-deletes the receipt and removes its "
                "stock from the on-hand total."
            )
            do_delete = st.form_submit_button(
                "Delete receipt",
                width="stretch",
                type="secondary",
                disabled=used > 0,
            )
        if do_delete:
            if not confirmed:
                st.error("Tick the confirmation to delete.")
                return
            if len(reason.strip()) < 3:
                st.error("A reason of at least 3 characters is required.")
                return
            if api_request(
                "DELETE",
                f"/inventory/receipts/{target['id']}",
                {"reason": reason.strip()},
            ):
                st.success("Receipt deleted. The action is logged.")
                st.rerun()

    with audit_tab:
        audit = api_request(
            "GET", f"/inventory/receipts/{target['id']}/audit"
        ) or []
        if not audit:
            st.caption("No corrections logged for this receipt yet.")
        else:
            for entry in audit:
                with st.expander(
                    f"{entry.get('action').title()} on "
                    f"{entry.get('created_at')[:19]} by "
                    f"{entry.get('actor_name') or 'unknown'}"
                ):
                    st.write(f"**Reason:** {entry.get('reason')}")
                    cols = st.columns(2)
                    cols[0].caption("Before")
                    cols[0].json(entry.get("snapshot_before") or {})
                    cols[1].caption("After")
                    if entry.get("snapshot_after"):
                        cols[1].json(entry["snapshot_after"])
                    else:
                        cols[1].write("— deleted —")


def init_job_card_state() -> None:
    st.session_state.setdefault("job_works", [])
    st.session_state.setdefault("job_items", [])
    st.session_state.setdefault("job_costs", [])
    st.session_state.setdefault("last_invoice_id", None)


def render_invoice_actions(invoice_id: int, key_prefix: str) -> None:
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Email invoice", width="stretch", key=f"{key_prefix}_email"):
            result = api_request("POST", f"/invoices/{invoice_id}/share-email")
            if result and result.get("emailed"):
                st.success("Invoice emailed.")
            elif result:
                st.warning("Invoice email was not sent. Check SMTP settings.")
    with c2:
        if st.button("WhatsApp invoice", width="stretch", key=f"{key_prefix}_whatsapp"):
            result = api_request("POST", f"/invoices/{invoice_id}/share-whatsapp")
            if result and result.get("whatsapp_url"):
                st.link_button("Open WhatsApp", result["whatsapp_url"], width="stretch")
    with c3:
        pdf = cached_invoice_pdf(invoice_id)
        if pdf:
            st.download_button(
                "Download PDF",
                data=pdf,
                file_name=f"invoice_{invoice_id}.pdf",
                mime="application/pdf",
                width="stretch",
                key=f"{key_prefix}_pdf",
            )


def display_line_items(lines: list[dict]) -> None:
    if not lines:
        st.caption("No line items yet.")
        return
    rows = [
        {
            "type": line.get("item"),
            "description": line.get("description"),
            "quantity": line.get("quantity"),
            "rate": line.get("rate"),
            "amount": line.get("amount"),
        }
        for line in lines
    ]
    st.dataframe(rows, hide_index=True, width="stretch")


def to_float(value) -> float:
    return float(value or 0)


def render_inventory_cost_history(receipts: list[dict]) -> None:
    st.subheader("Item cost history")
    if not receipts:
        st.caption("No stock receipts yet.")
        return

    item_names = sorted({receipt.get("item_name") for receipt in receipts if receipt.get("item_name")})
    selected_item_name = st.selectbox(
        "Compare purchase history for",
        item_names,
        key="inventory_cost_history_item",
    )
    item_receipts = [
        receipt for receipt in receipts
        if receipt.get("item_name") == selected_item_name and receipt.get("is_active", True)
    ]
    if not item_receipts:
        st.caption("No receipts found for this item.")
        return

    sorted_by_cost = sorted(item_receipts, key=lambda receipt: to_float(receipt.get("unit_cost")))
    cheapest = sorted_by_cost[0]
    latest = max(item_receipts, key=lambda receipt: receipt.get("created_at") or "")
    average_cost = sum(to_float(receipt.get("unit_cost")) for receipt in item_receipts) / len(item_receipts)

    metrics = st.columns(4)
    metrics[0].metric("Cheapest supplier", cheapest.get("supplier_name") or "Unknown")
    metrics[1].metric("Cheapest unit cost", f"KES {to_float(cheapest.get('unit_cost')):,.2f}")
    metrics[2].metric("Latest unit cost", f"KES {to_float(latest.get('unit_cost')):,.2f}")
    metrics[3].metric("Average unit cost", f"KES {average_cost:,.2f}")

    supplier_rows = []
    for supplier_name in sorted({receipt.get("supplier_name") or "Unknown" for receipt in item_receipts}):
        supplier_receipts = [
            receipt for receipt in item_receipts
            if (receipt.get("supplier_name") or "Unknown") == supplier_name
        ]
        supplier_rows.append(
            {
                "supplier": supplier_name,
                "best cost": min(to_float(receipt.get("unit_cost")) for receipt in supplier_receipts),
                "average cost": sum(to_float(receipt.get("unit_cost")) for receipt in supplier_receipts) / len(supplier_receipts),
                "latest cost": to_float(max(supplier_receipts, key=lambda receipt: receipt.get("created_at") or "").get("unit_cost")),
                "receipts": len(supplier_receipts),
            }
        )
    st.caption("Supplier comparison")
    st.dataframe(
        sorted(supplier_rows, key=lambda row: row["best cost"]),
        hide_index=True,
        width="stretch",
    )

    st.caption("Receipt history")
    history_rows = [
        {
            "date": str(receipt.get("created_at") or "")[:10],
            "supplier": receipt.get("supplier_name"),
            "quantity": receipt.get("quantity"),
            "remaining": receipt.get("remaining_quantity"),
            "unit cost": receipt.get("unit_cost"),
            "selling price": receipt.get("default_sale_price"),
            "reference": receipt.get("supplier_invoice_ref"),
            "invoice file": receipt.get("supplier_invoice_file_path"),
            "notes": receipt.get("notes"),
        }
        for receipt in sorted_by_cost
    ]
    st.dataframe(history_rows, hide_index=True, width="stretch")


def format_status(value: str | None) -> str:
    return (value or "").replace("_", " ").title()


def pending_job_cards(job_cards: list[dict]) -> list[dict]:
    return [
        job_card
        for job_card in job_cards
        if job_card.get("status") not in {"closed", "cancelled"}
        and job_card.get("is_active", True)
    ]


def open_job_card(job_card_id: int) -> None:
    st.session_state.active_job_card_id = job_card_id
    st.session_state.pending_nav_page = "Job Cards"
    st.rerun()


JOB_CARD_STATUS_OPTIONS_WITH_CANCELLED = [*JOB_CARD_STATUS_OPTIONS, "cancelled"]

# Statuses that lock further line-item editing. Mirrors the backend's
# ``_LOCKED_JOB_STATUSES``; keep in sync.
JOB_CARD_LOCKED_STATUSES = {"closed", "cancelled"}


def _present_items_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [piece.strip() for piece in value.split(",") if piece.strip()]


def _render_job_card_overview(detail: dict) -> None:
    """Read-only view — the default tab. Surfaces everything captured on the
    card so an operator can scan it without scrolling forever."""
    job_card = detail.get("job_card") or {}
    customer = detail.get("customer") or {}
    vehicle = detail.get("vehicle") or {}
    invoice = detail.get("invoice") or {}
    intake = detail.get("intake") or {}

    metrics = st.columns(4)
    metrics[0].metric("Status", format_status(job_card.get("status")))
    metrics[1].metric("Customer", customer.get("name") or "Not set")
    metrics[2].metric("Vehicle", vehicle.get("registration") or "Not set")
    metrics[3].metric(
        "Total",
        f"KES {float((invoice or {}).get('total_amount') or job_card.get('total_amount') or 0):,.2f}",
    )

    c1, c2 = st.columns(2)
    with c1:
        st.caption("Customer")
        st.write(customer.get("phone") or "No phone")
        st.write(customer.get("email") or "No email")
        st.write(
            customer.get("location_description") or customer.get("county") or "No location"
        )
    with c2:
        st.caption("Vehicle")
        st.write(
            " ".join(
                str(value) for value in [vehicle.get("make"), vehicle.get("type")] if value
            )
            or "Not set"
        )
        st.write(f"Year: {vehicle.get('year') or 'Not set'}")
        st.write(vehicle.get("description") or "No vehicle notes")

    # ---- Intake & condition ------------------------------------------------
    st.caption("Intake and condition")
    intake_cols = st.columns(4)
    intake_cols[0].markdown(
        f"**Arrival**<br/>{format_status(intake.get('intake_mode')) or '—'}",
        unsafe_allow_html=True,
    )
    intake_cols[1].markdown(
        f"**Delivery**<br/>{format_status(intake.get('delivery_mode')) or '—'}",
        unsafe_allow_html=True,
    )
    intake_cols[2].markdown(
        f"**Fuel**<br/>{intake.get('fuel_level') or '—'}",
        unsafe_allow_html=True,
    )
    intake_cols[3].markdown(
        f"**Mileage**<br/>{intake.get('mileage') or '—'}",
        unsafe_allow_html=True,
    )

    present_items = _present_items_list(intake.get("present_items"))
    other_present_items = intake.get("other_present_items")
    item_cols = st.columns(2)
    with item_cols[0]:
        st.caption("Items present in the car on arrival")
        if present_items:
            st.markdown("\n".join(f"- {item}" for item in present_items))
        else:
            st.write("— None recorded —")
    with item_cols[1]:
        st.caption("Other items / notes")
        st.write(other_present_items or "—")

    if intake.get("condition_notes"):
        st.caption("Condition notes")
        st.info(intake["condition_notes"])
    if intake.get("invoice_notes"):
        st.caption("Invoice notes")
        st.write(intake["invoice_notes"])

    # ---- Invoice snapshot --------------------------------------------------
    if invoice:
        st.caption("Invoice")
        invoice_cols = st.columns(4)
        invoice_cols[0].metric("Invoice", invoice.get("invoice_number") or "")
        invoice_cols[1].metric(
            "Net",
            f"KES {float(invoice.get('subtotal') or 0) - float(invoice.get('discount_amount') or 0):,.2f}",
        )
        invoice_cols[2].metric(
            "Tax", f"KES {float(invoice.get('vat_amount') or 0):,.2f}"
        )
        invoice_cols[3].metric(
            "Balance", f"KES {float(invoice.get('balance_due') or 0):,.2f}"
        )

    st.caption("Line items")
    display_line_items(detail.get("lines") or [])


def _render_job_card_edit_tab(detail: dict, job_card_id: int, key_prefix: str) -> None:
    """Admin-only editor for header fields + per-line add/remove.

    Locked once the card is closed or cancelled — matches the backend's
    ``_ensure_job_card_editable`` guard so the UI never shows controls that
    would then 409 on submit.
    """
    job_card = detail.get("job_card") or {}
    intake = detail.get("intake") or {}
    invoice = detail.get("invoice") or {}
    lines = detail.get("lines") or []

    if job_card.get("status") in JOB_CARD_LOCKED_STATUSES:
        st.warning(
            f"Card is **{format_status(job_card.get('status'))}** — line items "
            "are locked. Move the card back to an earlier state to edit."
        )
        return

    # ---- Header form -------------------------------------------------------
    st.markdown("##### Header")
    with st.form(f"{key_prefix}_header_form_{job_card_id}", clear_on_submit=False):
        c1, c2 = st.columns(2)
        intake_mode_value = intake.get("intake_mode") or "in_shop"
        intake_options = ["in_shop", "pickup", "dropoff", "pickup_and_dropoff"]
        intake_idx = (
            intake_options.index(intake_mode_value)
            if intake_mode_value in intake_options
            else 0
        )
        intake_mode = c1.selectbox(
            "Arrival", intake_options, index=intake_idx, format_func=format_status
        )
        delivery_mode_value = intake.get("delivery_mode") or "in_shop"
        delivery_idx = (
            intake_options.index(delivery_mode_value)
            if delivery_mode_value in intake_options
            else 0
        )
        delivery_mode = c2.selectbox(
            "Delivery", intake_options, index=delivery_idx, format_func=format_status
        )

        c3, c4 = st.columns(2)
        fuel_level = c3.text_input("Fuel level", value=intake.get("fuel_level") or "")
        mileage = c4.text_input("Mileage", value=intake.get("mileage") or "")

        present_items_text = st.text_input(
            "Items present (comma-separated)",
            value=intake.get("present_items") or "",
            help="Jumpers, jack, spare wheel, etc.",
        )
        other_present_items = st.text_area(
            "Other materials / accessories present",
            value=intake.get("other_present_items") or "",
        )
        condition_notes = st.text_area(
            "Condition notes", value=intake.get("condition_notes") or ""
        )
        invoice_notes = st.text_area(
            "Invoice notes", value=intake.get("invoice_notes") or ""
        )

        c5, c6 = st.columns(2)
        discount = c5.number_input(
            "Discount (KES)",
            min_value=0.0,
            value=float(invoice.get("discount_amount") or job_card.get("discount_amount") or 0),
            step=50.0,
        )
        tax_rate = c6.number_input(
            "Tax rate (%)",
            min_value=0.0,
            max_value=50.0,
            value=float(invoice.get("tax_rate") or 0),
            step=0.5,
        )

        save_header = st.form_submit_button(
            "Save header", width="stretch", type="primary"
        )

    if save_header:
        items_list = _present_items_list(present_items_text)
        result = api_request(
            "PATCH",
            f"/job-cards/{job_card_id}",
            {
                "intake_mode": intake_mode,
                "delivery_mode": delivery_mode,
                "fuel_level": fuel_level,
                "mileage": mileage,
                "present_items": items_list,
                "other_present_items": other_present_items,
                "condition_notes": condition_notes,
                "invoice_notes": invoice_notes,
                "discount_amount": discount,
                "tax_rate": tax_rate,
            },
        )
        if result is not None:
            st.success("Header updated.")
            st.rerun()

    st.divider()

    # ---- Add work / item / cost lines --------------------------------------
    st.markdown("##### Add line items")
    work_types = active_only(api_request("GET", "/work-types") or [])
    workers = active_only(api_request("GET", "/workers") or [])
    stock_items = active_only(api_request("GET", "/inventory/stock") or [])
    garage_costs = active_only(api_request("GET", "/garage-costs") or [])

    add_work_tab, add_item_tab, add_cost_tab = st.tabs(
        ["+ Work", "+ Part / Item", "+ Cost"]
    )

    with add_work_tab:
        with st.form(f"{key_prefix}_add_work_{job_card_id}", clear_on_submit=True):
            work_type = select_option(work_types, "Work type", key=f"{key_prefix}_work_type_{job_card_id}")
            worker = select_option(
                [{"id": w.get("id"), "name": w.get("full_name"), **w} for w in workers],
                "Mechanic (optional)",
                key=f"{key_prefix}_worker_{job_card_id}",
            )
            labour_amount = st.number_input(
                "Labour amount (KES)", min_value=0.0, value=0.0, step=50.0
            )
            notes = st.text_input("Notes", key=f"{key_prefix}_work_notes_{job_card_id}")
            submitted = st.form_submit_button("Add work", width="stretch", type="primary")
        if submitted:
            if not work_type:
                st.warning("Select a work type first.")
            else:
                result = api_request(
                    "POST",
                    f"/job-cards/{job_card_id}/works",
                    {
                        "work_type_id": work_type["id"],
                        "worker_id": worker["id"] if worker else None,
                        "labour_amount": labour_amount,
                        "notes": notes or None,
                    },
                )
                if result is not None:
                    st.success("Work line added.")
                    st.rerun()

    with add_item_tab:
        with st.form(f"{key_prefix}_add_item_{job_card_id}", clear_on_submit=True):
            stock = select_option(
                [
                    {
                        "id": s.get("garage_item_id"),
                        "name": (
                            f"{s.get('item_name')}  ·  on hand {s.get('quantity_on_hand', 0)}"
                        ),
                        **s,
                    }
                    for s in stock_items
                ],
                "Stock item",
                key=f"{key_prefix}_stock_{job_card_id}",
            )
            quantity = st.number_input(
                "Quantity", min_value=0.01, value=1.0, step=1.0, format="%.2f"
            )
            unit_price = st.number_input(
                "Unit price (KES)",
                min_value=0.0,
                value=stock_selling_price(stock),
                step=10.0,
            )
            notes = st.text_input("Notes", key=f"{key_prefix}_item_notes_{job_card_id}")
            submitted = st.form_submit_button("Add item", width="stretch", type="primary")
        if submitted:
            if not stock:
                st.warning("Select a stock item first.")
            else:
                result = api_request(
                    "POST",
                    f"/job-cards/{job_card_id}/items",
                    {
                        "garage_item_id": stock["id"],
                        "quantity": quantity,
                        "unit_price": unit_price,
                        "notes": notes or None,
                    },
                )
                if result is not None:
                    st.success("Item added.")
                    st.rerun()

    with add_cost_tab:
        with st.form(f"{key_prefix}_add_cost_{job_card_id}", clear_on_submit=True):
            cost_option = select_option(
                garage_costs, "Cost type", key=f"{key_prefix}_cost_{job_card_id}"
            )
            label = st.text_input(
                "Label",
                value=(cost_option or {}).get("name", ""),
                key=f"{key_prefix}_cost_label_{job_card_id}",
            )
            default_amount = float((cost_option or {}).get("default_amount") or 0)
            amount = st.number_input(
                "Amount (KES)", min_value=0.0, value=default_amount, step=50.0
            )
            submitted = st.form_submit_button("Add cost", width="stretch", type="primary")
        if submitted:
            if not label:
                st.warning("Give the cost a label.")
            else:
                result = api_request(
                    "POST",
                    f"/job-cards/{job_card_id}/costs",
                    {
                        "garage_cost_id": (cost_option or {}).get("id"),
                        "label": label,
                        "amount": amount,
                        "cost_type": (cost_option or {}).get("name"),
                    },
                )
                if result is not None:
                    st.success("Cost added.")
                    st.rerun()

    st.divider()

    # ---- Remove existing lines --------------------------------------------
    st.markdown("##### Remove existing lines")
    deletable = [
        line for line in lines if line.get("source") in {"work", "item", "cost"}
    ]
    if not deletable:
        st.caption("No removable line items on this card yet.")
        return
    for line in deletable:
        source = line.get("source")
        source_id = line.get("source_id")
        amount_label = f"KES {float(line.get('amount') or 0):,.2f}"
        cols = st.columns([4, 2, 1])
        cols[0].write(
            f"**{source.title()}** · {line.get('description')}"
        )
        cols[1].write(amount_label)
        if cols[2].button(
            "Remove",
            key=f"{key_prefix}_rm_{source}_{source_id}",
            type="secondary",
        ):
            api_request(
                "DELETE",
                f"/job-cards/{job_card_id}/{source}s/{source_id}",
            )
            st.rerun()


def _render_job_card_status_tab(detail: dict, job_card_id: int, key_prefix: str) -> None:
    job_card = detail.get("job_card") or {}
    invoice = detail.get("invoice") or {}

    if not require_admin_notice():
        return

    with st.form(f"{key_prefix}_status_form_{job_card_id}"):
        current_status = job_card.get("status") or "in_progress"
        options = JOB_CARD_STATUS_OPTIONS_WITH_CANCELLED
        status_index = (
            options.index(current_status) if current_status in options else 1
        )
        new_status = st.selectbox(
            "New status",
            options,
            index=status_index,
            format_func=format_status,
            key=f"{key_prefix}_status_select_{job_card_id}",
        )
        submitted = st.form_submit_button(
            "Update status", width="stretch", type="primary"
        )

    if submitted:
        result = api_request(
            "PATCH",
            f"/job-cards/{job_card_id}/status",
            {"status": new_status},
        )
        if result:
            if result.get("email_queued"):
                st.success("Status updated. Completion email queued for the customer.")
            elif new_status == "complete":
                st.warning(
                    "Status updated. No completion email was sent because the "
                    "customer has no email address."
                )
            elif new_status == "cancelled":
                refunded = result.get("refunded_movements", 0) or 0
                if refunded:
                    st.success(
                        f"Job card cancelled — refunded {refunded} stock "
                        "movement(s) back to inventory."
                    )
                else:
                    st.success("Job card cancelled.")
            else:
                st.success("Status updated.")
            st.session_state.active_job_card_id = job_card_id
            st.rerun()

    if invoice and invoice.get("id"):
        st.divider()
        st.markdown("##### Share invoice")
        render_invoice_actions(invoice["id"], f"{key_prefix}_share")


def render_job_card_detail_panel(job_card_id: int, key_prefix: str) -> None:
    detail = api_request("GET", f"/job-cards/{job_card_id}")
    if not detail:
        return

    job_card = detail.get("job_card") or {}
    st.subheader(job_card.get("job_number") or "Job card")

    overview_tab, edit_tab, status_tab = st.tabs(
        ["Overview", "Edit", "Status & Sharing"]
    )
    with overview_tab:
        _render_job_card_overview(detail)
    with edit_tab:
        if is_admin():
            _render_job_card_edit_tab(detail, job_card_id, key_prefix)
        else:
            st.info("Editing requires an admin account.")
    with status_tab:
        _render_job_card_status_tab(detail, job_card_id, key_prefix)


def stock_selling_price(stock_item: dict | None) -> float:
    if not stock_item:
        return 0.0
    sale_price = stock_item.get("latest_sale_price")
    if sale_price is not None and float(sale_price) > 0:
        return float(sale_price)
    unit_cost = stock_item.get("latest_unit_cost")
    return float(unit_cost) if unit_cost is not None else 0.0


def build_draft_lines(
    pickup_amount: float = 0.0,
    dropoff_amount: float = 0.0,
    discount_amount: float = 0.0,
    tax_rate: float = 0.0,
) -> tuple[list[dict], list[tuple[str, int | None]]]:
    rows = []
    mapping = []
    for index, item in enumerate(st.session_state.job_works):
        rows.append(
            {
                "type": "Work",
                "description": item.get("work_name", "Work"),
                "quantity": 1.0,
                "rate": float(item.get("labour_amount") or 0),
                "amount": float(item.get("labour_amount") or 0),
            }
        )
        mapping.append(("work", index))
    for index, item in enumerate(st.session_state.job_items):
        quantity = float(item.get("quantity") or 0)
        rate = float(item.get("unit_price") or 0)
        rows.append(
            {
                "type": "Item",
                "description": item.get("item_name", "Inventory item"),
                "quantity": quantity,
                "rate": rate,
                "amount": quantity * rate,
            }
        )
        mapping.append(("item", index))
    for index, item in enumerate(st.session_state.job_costs):
        rows.append(
            {
                "type": "Cost",
                "description": item.get("label", "Cost"),
                "quantity": 1.0,
                "rate": float(item.get("amount") or 0),
                "amount": float(item.get("amount") or 0),
            }
        )
        mapping.append(("cost", index))
    if pickup_amount:
        rows.append(
            {
                "type": "Pickup",
                "description": "Pickup cost",
                "quantity": 1.0,
                "rate": float(pickup_amount),
                "amount": float(pickup_amount),
            }
        )
        mapping.append(("pickup", None))
    if dropoff_amount:
        rows.append(
            {
                "type": "Dropoff",
                "description": "Dropoff cost",
                "quantity": 1.0,
                "rate": float(dropoff_amount),
                "amount": float(dropoff_amount),
            }
        )
        mapping.append(("dropoff", None))
    if discount_amount:
        discount = float(discount_amount)
        rows.append(
            {
                "type": "Discount",
                "description": "Discount",
                "quantity": 1.0,
                "rate": -discount,
                "amount": -discount,
            }
        )
        mapping.append(("discount", None))
    if tax_rate:
        taxable_total = max(
            0.0,
            sum(float(row.get("amount") or 0) for row in rows) if rows else 0.0,
        )
        tax_amount = taxable_total * float(tax_rate) / 100
        if tax_amount:
            rows.append(
                {
                    "type": "Tax",
                    "description": f"Tax ({float(tax_rate):g}%)",
                    "quantity": 1.0,
                    "rate": tax_amount,
                    "amount": tax_amount,
                }
            )
            mapping.append(("tax", None))
    return rows, mapping


def apply_draft_line_edits(edited_rows: list[dict], mapping: list[tuple[str, int | None]]) -> None:
    for row, (source, index) in zip(edited_rows, mapping, strict=False):
        quantity = max(float(row.get("quantity") or 0), 0)
        rate = max(float(row.get("rate") or 0), 0)
        amount = max(quantity * rate, 0)
        if source == "work" and index is not None:
            st.session_state.job_works[index]["labour_amount"] = amount
        elif source == "item" and index is not None:
            st.session_state.job_items[index]["quantity"] = quantity
            st.session_state.job_items[index]["unit_price"] = rate
            st.session_state.job_items[index]["amount"] = quantity * rate
        elif source == "cost" and index is not None:
            st.session_state.job_costs[index]["amount"] = amount


def render_line_item_editor(
    pickup_amount: float = 0.0,
    dropoff_amount: float = 0.0,
    discount_amount: float = 0.0,
    tax_rate: float = 0.0,
    key_prefix: str = "job_line_items",
) -> float:
    rows, mapping = build_draft_lines(pickup_amount, dropoff_amount, discount_amount, tax_rate)
    if not rows:
        st.caption("No line items yet.")
        return 0.0
    edited = st.data_editor(
        rows,
        hide_index=True,
        width="stretch",
        disabled=["type", "description", "amount"],
        column_config={
            "quantity": st.column_config.NumberColumn("Qty", min_value=0.0, step=1.0),
            "rate": st.column_config.NumberColumn("Rate", step=100.0),
            "amount": st.column_config.NumberColumn("Amount"),
        },
        key=f"{key_prefix}_editor",
    )
    total = sum(float(row.get("quantity") or 0) * float(row.get("rate") or 0) for row in edited)
    st.metric("Line total", f"KES {total:,.2f}")
    if st.button("Apply line edits", width="stretch", key=f"{key_prefix}_apply"):
        apply_draft_line_edits(edited, mapping)
        st.success("Line edits applied.")
        st.rerun()
    return total


def render_job_cards() -> None:
    st.title("Job Cards")
    init_job_card_state()

    customers = api_request("GET", "/customers") or []
    users = api_request("GET", "/users") or []
    workers = api_request("GET", "/workers") or []
    work_types = api_request("GET", "/work-types") or []
    costs = api_request("GET", "/garage-costs") or []
    stock = api_request("GET", "/inventory/stock") or []
    active_customers = active_only(customers)
    active_users = active_only(users)
    active_workers = active_only(workers)
    active_work_types = active_only(work_types)
    active_costs = active_only(costs)
    active_stock = active_only(stock)

    existing_cards = api_request("GET", "/job-cards") or []
    tabs = st.tabs(["View & Update", "Customer & Car", "Intake", "Work", "Items", "Line Items", "Invoice & Share"])

    customer_id = None
    vehicle_id = None
    new_customer_payload = None
    new_vehicle_payload = None
    selected_customer = None

    with tabs[0]:
        pending_cards = pending_job_cards(existing_cards)
        st.subheader("Pending job cards")
        if pending_cards:
            st.dataframe(
                [
                    {
                        "job card": item.get("job_number"),
                        "customer": item.get("customer_name"),
                        "vehicle": item.get("vehicle_registration"),
                        "status": format_status(item.get("status")),
                        "mileage": item.get("mileage"),
                        "invoice": item.get("invoice_number"),
                        "total": item.get("invoice_total") or item.get("total_amount"),
                    }
                    for item in pending_cards
                ],
                hide_index=True,
                width="stretch",
            )
            open_cols = st.columns(min(4, len(pending_cards)))
            for index, item in enumerate(pending_cards[:4]):
                if open_cols[index % len(open_cols)].button(
                    f"Open {item.get('job_number')}",
                    key=f"open_pending_job_{item['id']}",
                    width="stretch",
                ):
                    open_job_card(item["id"])
        else:
            st.caption("No pending job cards.")

        with st.expander("All job cards", expanded=False):
            filtered_cards = display_records(existing_cards, "job_cards_existing")
            selected_existing = select_record(
                filtered_cards,
                "Open saved job card lines",
                "existing_job_card_lines_select",
            )
            if selected_existing:
                lines = api_request("GET", f"/job-cards/{selected_existing['id']}/lines") or []
                display_line_items(lines)

        active_job_card_id = st.session_state.get("active_job_card_id")
        selected_job_card = select_record(
            existing_cards,
            "Open job card",
            f"job_card_update_select_{active_job_card_id or 'none'}",
            active_job_card_id,
        )
        if selected_job_card and selected_job_card.get("id") != active_job_card_id:
            active_job_card_id = selected_job_card["id"]
            st.session_state.active_job_card_id = active_job_card_id
            st.rerun()
        if active_job_card_id:
            render_job_card_detail_panel(active_job_card_id, "job_card_update")
        else:
            st.caption("No job card selected.")

    with tabs[1]:
        mode = st.radio("Customer", ["Existing customer", "New customer"], horizontal=True, key="job_customer_mode")
        if mode == "Existing customer":
            selected_customer = select_record(active_customers, "Customer", "job_customer_select")
            if selected_customer:
                customer_id = selected_customer["id"]
                vehicles = api_request("GET", f"/customer-vehicles?customer_id={customer_id}") or []
                vehicles = active_only(vehicles)
                car_mode = st.radio("Car", ["Existing car", "New car"], horizontal=True, key="job_car_mode")
                if car_mode == "Existing car":
                    selected_vehicle = select_record(vehicles, "Vehicle", "job_vehicle_select")
                    if selected_vehicle:
                        vehicle_id = selected_vehicle["id"]
                else:
                    new_vehicle_payload = vehicle_payload("job_new_vehicle", customer_id)
        else:
            st.caption("Creating a new customer here also creates the first car.")
            new_customer_payload = customer_payload("job_new_customer")

    with tabs[2]:
        intake_mode = st.selectbox("Arrival", ["in_shop", "pickup"], key="job_intake_mode")
        picked_up_by_user_id = None
        pickup_cost_id = None
        pickup_cost_amount = 0.0
        if intake_mode == "pickup":
            pickup_user = select_option(active_users, "Picked up by", "job_pickup_user", "full_name")
            pickup_cost = select_option(active_costs, "Pickup cost", "job_pickup_cost")
            picked_up_by_user_id = pickup_user["id"] if pickup_user else None
            pickup_cost_id = pickup_cost["id"] if pickup_cost else None
            pickup_cost_amount = st.number_input(
                "Pickup amount",
                min_value=0.0,
                value=float(pickup_cost.get("default_amount") or 0) if pickup_cost else 0.0,
                step=100.0,
                key="job_pickup_amount",
            )
        fuel_level = st.selectbox("Fuel level", ["Empty", "1/4", "1/2", "3/4", "Full"], key="job_fuel")
        mileage = st.text_input("Mileage", key="job_mileage")
        present_items = st.multiselect(
            "Items present in car",
            ["Jack", "Wheel spanner", "Jumpers", "Spare wheel", "Radio", "Fire extinguisher", "First aid kit"],
            key="job_present_items",
        )
        other_present_items = st.text_area("Other items/materials in car", key="job_other_present_items")
        condition_notes = st.text_area("Vehicle status/condition notes", key="job_condition_notes")

    with tabs[3]:
        selected_work_types = st.multiselect(
            "Work to be done",
            [work["name"] for work in active_work_types],
            key="job_work_types",
        )
        mechanic = select_option(active_workers, "Assigned mechanic", "job_mechanic", "full_name")
        labour_amount = st.number_input("Labour amount per selected work", min_value=0.0, step=100.0, key="job_labour_amount")
        work_notes = st.text_area("Work notes", key="job_work_notes")
        if st.button("Add selected work", width="stretch"):
            work_by_name = {work["name"]: work for work in active_work_types}
            for work_name in selected_work_types:
                work = work_by_name[work_name]
                st.session_state.job_works.append(
                    {
                        "work_type_id": work["id"],
                        "work_name": work_name,
                        "worker_id": mechanic["id"] if mechanic else None,
                        "mechanic": mechanic["full_name"] if mechanic else "",
                        "notes": work_notes,
                        "labour_amount": labour_amount,
                    }
                )
        display_records(st.session_state.job_works, "job_work_preview", page_size=6)
        if st.button("Clear work list", width="stretch"):
            st.session_state.job_works = []
            st.rerun()

    with tabs[4]:
        available_stock = [
            item for item in active_stock if float(item.get("quantity_on_hand") or 0) > 0
        ]
        stock_item = select_option(available_stock, "Inventory item", "job_stock_item", "item_name")
        price_signature = None
        if stock_item:
            st.caption(f"Available: {stock_item['quantity_on_hand']} {stock_item.get('unit') or ''}")
            price_signature = (
                stock_item.get("garage_item_id"),
                str(stock_item.get("latest_sale_price")),
                str(stock_item.get("latest_unit_cost")),
            )
        if st.session_state.get("job_item_price_signature") != price_signature:
            st.session_state.job_item_price = stock_selling_price(stock_item)
            st.session_state.job_item_price_signature = price_signature
        item_qty = st.number_input("Quantity used", min_value=0.01, step=1.0, key="job_item_qty")
        item_price = st.number_input(
            "Selling price",
            min_value=0.0,
            step=100.0,
            key="job_item_price",
        )
        item_notes = st.text_input("Item notes", key="job_item_notes")
        if st.button("Add item to job", width="stretch") and stock_item:
            st.session_state.job_items.append(
                {
                    "garage_item_id": stock_item["garage_item_id"],
                    "item_name": stock_item["item_name"],
                    "quantity": item_qty,
                    "unit_price": item_price,
                    "notes": item_notes,
                    "amount": item_qty * item_price,
                }
            )
        display_records(st.session_state.job_items, "job_items_preview", page_size=6)
        if st.button("Clear item list", width="stretch"):
            st.session_state.job_items = []
            st.rerun()

    with tabs[5]:
        st.subheader("Itemized draft lines")
        st.caption("Review and adjust quantities or rates before saving the invoice.")
        extra_cost = select_option(active_costs, "Add cost line", "job_line_extra_cost")
        extra_cost_amount = st.number_input(
            "Cost line amount",
            min_value=0.0,
            step=100.0,
            key="job_line_extra_cost_amount",
        )
        if st.button("Add cost line", width="stretch") and extra_cost and extra_cost_amount > 0:
            st.session_state.job_costs.append(
                {
                    "garage_cost_id": extra_cost["id"],
                    "label": extra_cost["name"],
                    "amount": extra_cost_amount,
                    "cost_type": "other",
                }
            )
            st.rerun()
        render_line_item_editor(
            pickup_amount=float(pickup_cost_amount or 0),
            key_prefix="job_draft_lines",
        )

    with tabs[6]:
        delivery_mode = st.selectbox("Completion delivery", ["in_shop", "dropoff"], key="job_delivery_mode")
        dropoff_cost_id = None
        dropoff_cost_amount = 0.0
        if delivery_mode == "dropoff":
            dropoff_cost = select_option(active_costs, "Dropoff cost", "job_dropoff_cost")
            dropoff_cost_id = dropoff_cost["id"] if dropoff_cost else None
            dropoff_cost_amount = st.number_input(
                "Dropoff amount",
                min_value=0.0,
                value=float(dropoff_cost.get("default_amount") or 0) if dropoff_cost else 0.0,
                step=100.0,
                key="job_dropoff_amount",
            )

        invoice_type = st.selectbox("Invoice type", ["intermediate", "final"], key="job_invoice_type")
        discount_amount = st.number_input("Discount", min_value=0.0, step=100.0, key="job_discount")
        include_tax = st.checkbox("Include tax", key="job_include_tax")
        tax_rate = 0.0
        if include_tax:
            tax_rate = st.number_input(
                "Tax percentage",
                min_value=0.0,
                value=16.0,
                step=1.0,
                key="job_tax_rate",
            )
        invoice_notes = st.text_area("Invoice notes", key="job_invoice_notes")

        work_total = sum(float(item.get("labour_amount") or 0) for item in st.session_state.job_works)
        items_total = sum(float(item.get("amount") or 0) for item in st.session_state.job_items)
        costs_total = sum(float(item.get("amount") or 0) for item in st.session_state.job_costs)
        pickup_total = float(pickup_cost_amount or 0)
        dropoff_total = float(dropoff_cost_amount or 0)
        subtotal = work_total + items_total + costs_total + pickup_total + dropoff_total
        net_amount = max(0.0, subtotal - float(discount_amount or 0))
        tax_amount = net_amount * float(tax_rate or 0) / 100
        total = net_amount + tax_amount

        st.subheader("Final line check")
        st.caption("These are the lines that will be saved and shared on the invoice.")
        render_line_item_editor(
            pickup_amount=float(pickup_cost_amount or 0),
            dropoff_amount=float(dropoff_cost_amount or 0),
            discount_amount=float(discount_amount or 0),
            tax_rate=float(tax_rate or 0),
            key_prefix="job_final_lines",
        )

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Net amount", f"KES {net_amount:,.2f}")
        c2.metric("Tax", f"KES {tax_amount:,.2f}")
        c3.metric("Gross total", f"KES {total:,.2f}")
        c4.metric("Items", f"KES {items_total:,.2f}")

        if st.button("Save job card and invoice", width="stretch"):
            payload = {
                "customer_id": customer_id,
                "new_customer": new_customer_payload,
                "vehicle_id": vehicle_id,
                "new_vehicle": new_vehicle_payload,
                "intake_mode": intake_mode,
                "picked_up_by_user_id": picked_up_by_user_id,
                "pickup_cost_id": pickup_cost_id,
                "pickup_cost_amount": pickup_cost_amount,
                "delivery_mode": delivery_mode,
                "dropoff_cost_id": dropoff_cost_id,
                "dropoff_cost_amount": dropoff_cost_amount,
                "fuel_level": fuel_level,
                "mileage": mileage,
                "present_items": present_items,
                "other_present_items": other_present_items,
                "condition_notes": condition_notes,
                "works": [
                    {k: v for k, v in item.items() if k not in ("work_name", "mechanic")}
                    for item in st.session_state.job_works
                ],
                "inventory_items": [
                    {k: v for k, v in item.items() if k not in ("item_name", "amount")}
                    for item in st.session_state.job_items
                ],
                "additional_costs": st.session_state.job_costs,
                "invoice_type": invoice_type,
                "invoice_notes": invoice_notes,
                "discount_amount": discount_amount,
                "tax_rate": tax_rate,
            }
            invoice = api_request("POST", "/job-cards", payload)
            if invoice:
                st.session_state.last_invoice_id = invoice["id"]
                st.session_state.job_works = []
                st.session_state.job_items = []
                st.session_state.job_costs = []
                st.success(f"Invoice {invoice['invoice_number']} saved.")

        if st.session_state.last_invoice_id:
            st.subheader("Share saved invoice")
            render_invoice_actions(st.session_state.last_invoice_id, "last_invoice")


def render_invoices() -> None:
    st.title("Invoices")
    st.caption("Track payment status, record receipts, and share invoices.")
    invoices = api_request("GET", "/invoices") or []

    # Inject the status badge into the rendered list so the table is scannable
    # without overwhelming the data display helper.
    enriched = [
        {**invoice, "payment_status": _status_badge(invoice.get("payment_status"))}
        for invoice in invoices
    ]
    filtered_enriched = display_records(
        enriched, "invoices",
        empty_message="No invoices have been raised yet. Open a job card to issue one.",
    )
    # Map filtered list back to the original objects (display_records returns
    # filtered enriched rows; we want the originals for selection).
    by_id = {inv["id"]: inv for inv in invoices}
    filtered = [by_id[row["id"]] for row in filtered_enriched if "id" in row]

    selected = select_record(filtered, "Invoice", "invoice_select")
    if not selected:
        return

    total = float(selected.get("total_amount") or 0)
    paid = float(selected.get("amount_paid") or 0)
    balance = float(selected.get("balance_due") or 0)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total", f"KES {total:,.2f}")
    c2.metric("Paid", f"KES {paid:,.2f}")
    c3.metric(
        "Balance",
        f"KES {balance:,.2f}",
        delta=("Fully paid" if balance <= 0 else f"-KES {balance:,.2f}"),
        delta_color="off" if balance <= 0 else "inverse",
    )
    c4.metric("Status", _status_badge(selected.get("payment_status")))

    st.divider()
    st.markdown("#### Record a payment")
    with st.form(f"invoice_payment_{selected['id']}"):
        cols = st.columns([2, 1])
        with cols[0]:
            amount_paid = st.number_input(
                "Total amount received (cumulative)",
                min_value=0.0,
                value=paid,
                step=100.0,
                key=f"invoice_amount_paid_{selected['id']}",
                help=(
                    "Enter the running total received against this invoice — "
                    "the system derives the payment status from it."
                ),
            )
        with cols[1]:
            st.caption("Tip")
            st.write(
                "Status is derived from the amount paid versus the total — "
                "you don't need to set it manually."
            )
        if st.form_submit_button("Record payment", width="stretch", type="primary"):
            result = api_request(
                "PATCH",
                f"/invoices/{selected['id']}/payment",
                {"amount_paid": amount_paid},
            )
            if result:
                st.success("Payment recorded.")
                st.rerun()

    st.divider()
    st.markdown("#### Invoice lines")
    lines = api_request("GET", f"/invoices/{selected['id']}/lines") or []
    display_line_items(lines)

    st.divider()
    st.markdown("#### Share")
    render_invoice_actions(selected["id"], f"invoice_{selected['id']}")


def _parse_iso_dt(value) -> datetime | None:
    """Best-effort ISO-8601 parse — the API returns timezone-aware strings,
    but tests and older rows occasionally don't."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _age_in_days(value) -> int | None:
    parsed = _parse_iso_dt(value)
    if parsed is None:
        return None
    now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
    return max(0, (now - parsed).days)


_STATUS_PALETTE = {
    "draft": "🟤 Draft",
    "in_progress": "🔵 In progress",
    "ready": "🟢 Ready",
    "invoiced": "🟣 Invoiced",
    "complete": "🟢 Complete",
    "closed": "⚫ Closed",
    "cancelled": "🔴 Cancelled",
    "not_paid": "🔴 Not paid",
    "partially_paid": "🟠 Partial",
    "paid": "🟢 Paid",
}


def _status_badge(value: str | None) -> str:
    return _STATUS_PALETTE.get((value or "").lower(), format_status(value))


def render_dashboard() -> None:
    """Operational dashboard.

    Layout principle: most-actionable info at the top (what needs my
    attention today?), trend / analysis at the bottom. Heavy lists are
    server-filtered + limited so the page stays snappy on a busy garage.
    """
    st.title(f"{APP_NAME} Dashboard")
    st.caption(f"Last refreshed {datetime.now():%a %d %b %Y %H:%M}")

    # ---- Fetch the minimal data the panel actually needs ----------------
    invoices = api_request("GET", "/invoices") or []
    job_stats = api_request("GET", "/job-cards/stats") or {
        "total": 0,
        "by_status": {},
        "pending": 0,
    }
    stock = api_request("GET", "/inventory/stock") or []

    pending_total = int(job_stats.get("pending", 0))
    pending_cards = api_request(
        "GET",
        "/job-cards/recent"
        "?status_filter=draft,in_progress,ready,invoiced,complete&limit=20",
    ) or []
    pending_cards = [c for c in pending_cards if c.get("is_active", True)]

    # ---- Derived metrics ------------------------------------------------
    today = date.today()
    open_invoices = [
        invoice
        for invoice in invoices
        if invoice.get("payment_status") in ("not_paid", "partially_paid")
        and float(invoice.get("balance_due") or 0) > 0
    ]
    receivable_total = sum(float(inv.get("balance_due") or 0) for inv in open_invoices)
    paid_today = sum(
        float(inv.get("amount_paid") or 0)
        for inv in invoices
        if _parse_iso_dt(inv.get("updated_at") or inv.get("created_at"))
        and (_parse_iso_dt(inv.get("updated_at") or inv.get("created_at")).date() == today)
    )
    new_jobs_today = sum(
        1
        for card in pending_cards
        if _parse_iso_dt(card.get("created_at"))
        and _parse_iso_dt(card.get("created_at")).date() == today
    )
    low_stock = [
        item for item in stock if float(item.get("quantity_on_hand") or 0) <= 5
    ]
    stock_value = sum(
        float(item.get("quantity_on_hand") or 0)
        * float(item.get("latest_unit_cost") or 0)
        for item in stock
    )

    # ---- Top KPI strip — five most actionable numbers -------------------
    st.markdown("#### At a glance")
    kpi_cols = st.columns(5)
    kpi_cols[0].metric("Pending jobs", pending_total)
    kpi_cols[1].metric(
        "Open receivables",
        f"KES {receivable_total:,.0f}",
        f"{len(open_invoices)} invoice(s)",
    )
    kpi_cols[2].metric("New jobs today", new_jobs_today)
    kpi_cols[3].metric("Low stock items", len(low_stock))
    kpi_cols[4].metric("Stock cost value", f"KES {stock_value:,.0f}")
    if paid_today:
        st.caption(f"📥 KES {paid_today:,.0f} received today across all invoices.")

    st.divider()

    # ---- Aging receivables ----------------------------------------------
    if open_invoices:
        st.markdown("#### Aging receivables")
        buckets = {"0–30 days": 0.0, "31–60 days": 0.0, "61–90 days": 0.0, "90+ days": 0.0}
        for invoice in open_invoices:
            balance = float(invoice.get("balance_due") or 0)
            age = _age_in_days(invoice.get("created_at")) or 0
            if age <= 30:
                buckets["0–30 days"] += balance
            elif age <= 60:
                buckets["31–60 days"] += balance
            elif age <= 90:
                buckets["61–90 days"] += balance
            else:
                buckets["90+ days"] += balance
        bucket_cols = st.columns(4)
        for col, (label, amount) in zip(bucket_cols, buckets.items(), strict=True):
            col.metric(label, f"KES {amount:,.0f}")
        st.divider()

    # ---- Pending job cards ----------------------------------------------
    header_cols = st.columns([3, 1])
    header_cols[0].markdown("#### Pending job cards")
    if pending_total:
        header_cols[1].metric("Total pending", pending_total)

    pending_status_filter = st.selectbox(
        "Filter by status",
        ["All pending", "draft", "in_progress", "ready", "invoiced", "complete"],
        index=0,
        key="dashboard_pending_status_filter",
        format_func=(
            lambda value: "All pending" if value == "All pending" else format_status(value)
        ),
    )
    visible_cards = pending_cards
    if pending_status_filter != "All pending":
        visible_cards = [
            card for card in pending_cards
            if card.get("status") == pending_status_filter
        ]

    if visible_cards:
        st.dataframe(
            [
                {
                    "job card": item.get("job_number"),
                    "customer": item.get("customer_name") or "—",
                    "vehicle": item.get("vehicle_registration") or "—",
                    "status": _status_badge(item.get("status")),
                    "invoice": item.get("invoice_number") or "—",
                    "total": float(
                        item.get("invoice_total") or item.get("total_amount") or 0
                    ),
                    "age (days)": _age_in_days(item.get("created_at")),
                }
                for item in visible_cards
            ],
            hide_index=True,
            width="stretch",
            column_config={
                "total": st.column_config.NumberColumn(format="KES %,.2f"),
                "age (days)": st.column_config.NumberColumn(format="%d"),
            },
        )
        if pending_total > len(pending_cards):
            st.caption(
                f"Showing the most-recent {len(pending_cards)} of "
                f"{pending_total} pending cards. Use the **Job Cards** page "
                "for the full list and search."
            )
        quick_open = visible_cards[:4]
        if quick_open:
            open_cols = st.columns(len(quick_open))
            for index, item in enumerate(quick_open):
                if open_cols[index].button(
                    f"Open {item.get('job_number')}",
                    key=f"dashboard_open_job_{item['id']}",
                    width="stretch",
                ):
                    open_job_card(item["id"])
    else:
        st.caption("No pending job cards.")

    st.divider()

    # ---- Attention lists ------------------------------------------------
    a1, a2 = st.columns(2)
    with a1:
        st.markdown("#### Top open invoices")
        if open_invoices:
            top = sorted(
                open_invoices,
                key=lambda inv: float(inv.get("balance_due") or 0),
                reverse=True,
            )[:8]
            st.dataframe(
                [
                    {
                        "invoice": inv.get("invoice_number"),
                        "status": _status_badge(inv.get("payment_status")),
                        "balance": float(inv.get("balance_due") or 0),
                        "age": _age_in_days(inv.get("created_at")),
                    }
                    for inv in top
                ],
                hide_index=True,
                width="stretch",
                column_config={
                    "balance": st.column_config.NumberColumn(format="KES %,.2f"),
                    "age": st.column_config.NumberColumn(format="%d d"),
                },
            )
        else:
            st.caption("All invoices are settled. 🎉")
    with a2:
        st.markdown("#### Low stock")
        if low_stock:
            st.dataframe(
                [
                    {
                        "item": item.get("item_name"),
                        "on hand": float(item.get("quantity_on_hand") or 0),
                        "unit": item.get("unit") or "",
                        "supplier": item.get("latest_supplier") or "—",
                        "latest cost": float(item.get("latest_unit_cost") or 0),
                    }
                    for item in low_stock[:10]
                ],
                hide_index=True,
                width="stretch",
                column_config={
                    "latest cost": st.column_config.NumberColumn(format="KES %,.2f"),
                },
            )
        else:
            st.caption("No items below the 5-unit threshold.")

    st.divider()

    # ---- Money + analysis ----------------------------------------------
    st.markdown("#### Analysis")
    total_invoiced = sum(float(invoice.get("total_amount") or 0) for invoice in invoices)
    total_paid = sum(float(invoice.get("amount_paid") or 0) for invoice in invoices)
    summary_cols = st.columns(3)
    summary_cols[0].metric("Lifetime invoiced", f"KES {total_invoiced:,.0f}")
    summary_cols[1].metric("Lifetime received", f"KES {total_paid:,.0f}")
    summary_cols[2].metric(
        "Outstanding", f"KES {max(0.0, total_invoiced - total_paid):,.0f}"
    )

    status_cols = st.columns(2)
    invoice_status: dict[str, int] = {}
    for invoice in invoices:
        bucket = invoice.get("payment_status") or "not_paid"
        invoice_status[bucket] = invoice_status.get(bucket, 0) + 1
    job_status_counts = dict(job_stats.get("by_status") or {})
    with status_cols[0]:
        st.caption("Invoice payment status")
        if invoice_status:
            st.bar_chart({"count": invoice_status})
        else:
            st.caption("No invoices yet.")
    with status_cols[1]:
        st.caption("Job card status")
        if job_status_counts:
            st.bar_chart({"count": job_status_counts})
        else:
            st.caption("No job cards yet.")


def render_settings() -> None:
    st.title("Settings")
    section = st.sidebar.selectbox(
        "Setup section",
        [
            "Suppliers",
            "Users",
            "Workers",
            "Customers",
            "Work",
            "Costs",
            "Garage Items",
            "Email",
        ],
    )

    if section == "Suppliers":
        render_suppliers()
    elif section == "Users":
        render_users()
    elif section == "Workers":
        render_workers()
    elif section == "Customers":
        render_customers()
    elif section == "Work":
        render_named_records("Work", "/work-types")
    elif section == "Costs":
        render_named_records("Cost", "/garage-costs")
    elif section == "Garage Items":
        render_garage_items()
    elif section == "Email":
        render_email_settings()


def _initials(full_name: str | None) -> str:
    """First-letter initials for the sidebar avatar pill."""
    if not full_name:
        return "?"
    parts = [p for p in full_name.strip().split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


def main_app() -> None:
    pending_nav_page = st.session_state.pop("pending_nav_page", None)
    if pending_nav_page:
        st.session_state.nav_page = pending_nav_page
    st.session_state.pop("main_page", None)

    role = st.session_state.get("role") or "user"
    full_name = st.session_state.get("full_name") or "Signed-in user"

    # The avatar pill is the only place where we *want* a fixed colour —
    # the blue circle reads on both light and dark backgrounds. Everything
    # else (name, role tag) inherits the theme's text colour so dark mode
    # doesn't black out the sidebar.
    role_tint = (
        "rgba(59, 130, 246, 0.20)" if role == "admin" else "rgba(127, 127, 127, 0.22)"
    )
    with st.sidebar:
        st.markdown(
            f"""
            <div style="display:flex; align-items:center; gap:12px; padding: 4px 0 16px 0;">
              <div style="
                width:42px; height:42px; border-radius:50%;
                background:#1d4ed8; color:#ffffff; font-weight:600;
                display:flex; align-items:center; justify-content:center;
                box-shadow: 0 1px 2px rgba(0, 0, 0, 0.20);
              ">{_initials(full_name)}</div>
              <div style="line-height: 1.2;">
                <div style="font-weight:600;">{full_name}</div>
                <div style="font-size:12px; opacity:0.75; margin-top: 2px;">
                  <span style="
                    background: {role_tint};
                    padding: 2px 10px; border-radius: 999px;
                    font-weight:600; letter-spacing: 0.02em;
                  ">{role}</span>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        page = st.radio(
            "Navigation",
            ["Dashboard", "Job Cards", "Inventory", "Invoices", "Settings"],
            label_visibility="collapsed",
            key="nav_page",
        )
        st.divider()
        if st.button("Sign out", width="stretch"):
            st.session_state.clear()
            st.query_params.clear()
            st.rerun()
        st.caption(f"v0.1 · {APP_NAME}")

    if page == "Dashboard":
        render_dashboard()
    elif page == "Job Cards":
        render_job_cards()
    elif page == "Inventory":
        render_inventory()
    elif page == "Invoices":
        render_invoices()
    else:
        render_settings()


restore_login_from_url()

if "token" not in st.session_state:
    login_screen()
else:
    main_app()
