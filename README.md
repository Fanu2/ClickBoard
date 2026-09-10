# My Links Hub v2

Improved Streamlit version of the original My Links Dashboard.

## Improvements
- Dashboard metrics
- Search across names, URLs and categories
- Category navigation
- Sorting by name, category or domain
- Card-based project/link layout
- Add-link form with URL validation
- Delete controls
- Responsive Streamlit layout

## Run

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

This version deliberately remains simple and session-based like the original. A future version can add persistent JSON/SQLite storage without changing the visual concept.
