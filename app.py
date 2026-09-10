import streamlit as st
from urllib.parse import urlparse

st.set_page_config(page_title="My Links Hub", page_icon="🌐", layout="wide")

CATEGORIES = ["Repositories", "Spaces", "Vercel", "Streamlit Apps", "Bookmarks"]

DEFAULT_LINKS = {
    "Repositories": [
        {"name": "My GitHub Repo", "url": "https://github.com/yourusername/yourrepo"},
        {"name": "Another Repo", "url": "https://github.com/yourusername/anotherrepo"},
    ],
    "Spaces": [
        {"name": "HuggingFace Space 1", "url": "https://huggingface.co/spaces/username/space1"}
    ],
    "Vercel": [
        {"name": "My Vercel Project", "url": "https://myproject.vercel.app"}
    ],
    "Streamlit Apps": [
        {"name": "My Streamlit App", "url": "https://share.streamlit.io/username/appname"}
    ],
    "Bookmarks": [
        {"name": "Useful Article", "url": "https://example.com/article"}
    ],
}

if "links" not in st.session_state:
    st.session_state.links = {k: list(v) for k, v in DEFAULT_LINKS.items()}

if "selected_category" not in st.session_state:
    st.session_state.selected_category = "All"

st.markdown("""
<style>
.block-container{padding-top:2rem;padding-bottom:2rem}
.hero{padding:25px 28px;border-radius:18px;background:linear-gradient(135deg,#17233a,#243c68);color:white;margin-bottom:20px}
.hero h1{margin:0;font-size:34px}.hero p{margin:7px 0 0;color:#b9c8df}
.metric{padding:16px;border-radius:14px;background:#f5f7fb;border:1px solid #e2e7ef}
.card{padding:17px;border-radius:14px;border:1px solid #e1e6ef;background:white;min-height:145px;margin-bottom:12px}
.card h3{margin:0 0 6px}.muted{color:#697586;font-size:13px}.pill{display:inline-block;padding:4px 9px;border-radius:20px;background:#edf3ff;color:#315b9a;font-size:11px;margin-top:8px}
</style>
""", unsafe_allow_html=True)

def valid_url(url):
    try:
        p = urlparse(url.strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False

def domain(url):
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""

def all_links():
    return [(cat, link) for cat in CATEGORIES for link in st.session_state.links.get(cat, [])]

def remove_link(category, index):
    st.session_state.links[category].pop(index)

total = sum(len(st.session_state.links.get(c, [])) for c in CATEGORIES)
domains = len({domain(l["url"]) for _, l in all_links() if domain(l["url"])})

st.markdown("""
<div class="hero">
<h1>🌐 My Links Hub</h1>
<p>A personal dashboard for projects, apps, services and useful web resources.</p>
</div>
""", unsafe_allow_html=True)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total Links", total)
m2.metric("Categories", len(CATEGORIES))
m3.metric("Domains", domains)
m4.metric("Projects & Apps", sum(len(st.session_state.links.get(c, [])) for c in CATEGORIES if c != "Bookmarks"))

st.sidebar.header("➕ Add Link")
with st.sidebar.form("add_link_form", clear_on_submit=True):
    new_name = st.text_input("Link name")
    new_url = st.text_input("URL")
    new_category = st.selectbox("Category", CATEGORIES)
    submitted = st.form_submit_button("Add Link", type="primary")

    if submitted:
        if not new_name.strip() or not new_url.strip():
            st.error("Please provide both name and URL.")
        elif not valid_url(new_url):
            st.error("Please enter a valid http:// or https:// URL.")
        else:
            st.session_state.links[new_category].append(
                {"name": new_name.strip(), "url": new_url.strip()}
            )
            st.success(f"Added '{new_name.strip()}' to {new_category}.")
            st.rerun()

st.sidebar.divider()
st.sidebar.header("🔎 Navigation")
selected = st.sidebar.radio("Category", ["All"] + CATEGORIES, index=["All"] + CATEGORIES.index(st.session_state.selected_category) if st.session_state.selected_category != "All" else 0)
st.session_state.selected_category = selected

query = st.text_input("🔍 Search links", placeholder="Search by name, URL or category…")
sort_mode = st.selectbox("Sort", ["Name", "Category", "Domain"], horizontal=True)

items = all_links()
if selected != "All":
    items = [(c, l) for c, l in items if c == selected]

q = query.strip().lower()
if q:
    items = [(c, l) for c, l in items if q in f"{c} {l['name']} {l['url']}".lower()]

if sort_mode == "Name":
    items.sort(key=lambda x: x[1]["name"].lower())
elif sort_mode == "Category":
    items.sort(key=lambda x: (x[0], x[1]["name"].lower()))
else:
    items.sort(key=lambda x: domain(x[1]["url"]).lower())

st.subheader(f"{selected} — {len(items)} links")

if not items:
    st.info("No links match the current search/filter.")
else:
    for start in range(0, len(items), 3):
        cols = st.columns(3)
        for col, (category, link) in zip(cols, items[start:start+3]):
            with col:
                st.markdown(f"""
<div class="card">
<h3>{link["name"]}</h3>
<div class="muted">{domain(link["url"])}</div>
<span class="pill">{category}</span>
</div>
""", unsafe_allow_html=True)
                b1, b2 = st.columns(2)
                with b1:
                    st.link_button("Open ↗", link["url"], use_container_width=True)
                with b2:
                    key = f"delete_{category}_{start}_{link['name']}"
                    if st.button("Delete", key=key, use_container_width=True):
                        idx = st.session_state.links[category].index(link)
                        remove_link(category, idx)
                        st.rerun()

st.divider()
st.caption("My Links Hub v2 · Session-based storage — links reset when the Streamlit session restarts.")
