"""Simple Streamlit interface with expandable evidence and tool transparency."""
import httpx
import ollama
import streamlit as st

from src.agent import ask
from src.config import settings

st.set_page_config(page_title="FinCopilot", page_icon="📊", layout="wide")
st.title("FinCopilot")
st.caption("Ask in everyday words. FinCopilot explains finance terms simply, and shows the company-report source behind its answers.")
with st.sidebar:
    st.header("Optional filters")
    company = st.selectbox("Company", ["All", "HPE", "MSFT", "AAPL", "NVDA", "ACN"])
    year = st.text_input("Report year (optional)", placeholder="For example, 2025", help="Leave blank to search all available years.")
    st.info("You can ask normal questions, too. Company answers use the reports and data available in this project.")
    st.caption("For learning and research only; not investment advice.")
question = st.chat_input("Ask in your own words. Try: What does revenue mean?")
if question:
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"), st.spinner("Thinking it through and checking sources when needed..."):
        try:
            result = ask(question, None if company == "All" else company, year or None)
            st.write(result["answer"])
            with st.expander("Sources"):
                for source in result["citations"]:
                    st.markdown(f"**[{source['id']}] {source['company']} FY{source['fiscal_year']} · {source['section']}**")
                    st.caption(source["source_file"])
                    st.write(source["text"])
            with st.expander("Tools used"):
                st.json({"tools": result["tools_used"], "results": result["tool_results"]})
        except Exception as exc:  # noqa: BLE001 - present runtime failures in the chat panel
            if isinstance(exc, httpx.HTTPError):
                st.error("The local Ollama service is unavailable. Start Ollama, then retry.")
                st.caption(str(exc))
            elif isinstance(exc, ollama.ResponseError) and settings.ollama_model.endswith(":cloud"):
                st.error("The configured Ollama cloud model could not answer.")
                st.caption(
                    "Run `ollama signin`, then retry. Cloud availability also depends on your account's included usage."
                )
            elif isinstance(exc, (ollama.RequestError, ollama.ResponseError)):
                st.error("The configured Ollama model is unavailable. Pull it with `ollama pull <model>`.")
                st.caption(str(exc))
            else:
                st.error(str(exc))
