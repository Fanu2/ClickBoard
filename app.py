import streamlit as st

# Categories of links
CATEGORIES = ["Repositories", "Spaces", "Vercel", "Streamlit Apps", "Bookmarks"]

# Initialize session state for links if not present
if "links" not in st.session_state:
    st.session_state.links = {
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

st.title("🌐 My Links Dashboard")

# Sidebar: Add new link
st.sidebar.header("Add a New Link")
new_name = st.sidebar.text_input("Link Name")
new_url = st.sidebar.text_input("URL")
new_category = st.sidebar.selectbox("Category", CATEGORIES)
add_link_btn = st.sidebar.button("Add Link")

if add_link_btn:
    if new_name and new_url:
        st.session_state.links[new_category].append({"name": new_name, "url": new_url})
        st.sidebar.success(f"Added '{new_name}' to {new_category}")
    else:
        st.sidebar.error("Please provide both name and URL")

# Show links by category
for category in CATEGORIES:
    st.subheader(category)
    links = st.session_state.links.get(category, [])
    if not links:
        st.write("_No links added yet._")
    else:
        for link in links:
            st.markdown(f"- [{link['name']}]({link['url']})", unsafe_allow_html=True)
