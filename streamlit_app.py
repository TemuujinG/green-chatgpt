import base64
import hmac
import openai
import os
import pandas as pd
import streamlit as st
import tempfile
import uuid
from langchain.callbacks.base import BaseCallbackHandler
from langchain.memory import AstraDBChatMessageHistory
from langchain.memory import ConversationBufferWindowMemory
from langchain.prompts import ChatPromptTemplate
from langchain.schema import HumanMessage, AIMessage
from langchain.schema import StrOutputParser
from langchain.schema.runnable import RunnableMap
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader, CSVLoader, WebBaseLoader
from langchain_community.vectorstores import AstraDB
from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings
from pathlib import Path

print("Started")
st.set_page_config(page_title='Green loan l geneshvv', page_icon='☘')

st.markdown(f"""
    <style>
    div[data-testid="stToolbar"] {{
                visibility: hidden;
                height: 0%;
                position: fixed;
                }}
                div[data-testid="stDecoration"] {{
                visibility: hidden;
                height: 0%;
                position: fixed;
                }}
                div[data-testid="stStatusWidget"] {{
                visibility: hidden;
                height: 0%;
                position: fixed;
                }}
                #MainMenu {{
                visibility: hidden;
                height: 0%;
                }}
                header {{
                visibility: hidden;
                height: 0%;
                }}
                footer {{
                visibility: hidden;
                height: 0%;
                }}
    [data-testid="stApp"]  > div {{
          background: url(data:image/png;base64,{base64.b64encode(open('.assets/chatbot.png', "rb").read()).decode()});
            background-repeat: no-repeat;
            background-size: cover;
      }}
      [data-testid="stBottom"]  > div {{
          background: url(data:image/png;base64,{base64.b64encode(open('.assets/chatbot.png', "rb").read()).decode()});
            background-size: 0;
      }}
      [data-testid="stHeader"] > {{display:none; margin:-2em}}
        .reportview-container {{
            margin-top: -2em;
        }}
        #MainMenu {{visibility: hidden;}}
        .stDeployButton {{display:none;}}
        footer {{visibility: hidden;}}
        header {{display: none;}}
        #stDecoration {{display:none;}}
    </style>
""", unsafe_allow_html=True)
# Get a unique session id for memory
if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4()


# Streaming call back handler for responses
class StreamHandler(BaseCallbackHandler):
    def __init__(self, container, initial_text=""):
        self.container = container
        self.text = initial_text

    def on_llm_new_token(self, token: str, **kwargs):
        self.text += token
        self.container.markdown(self.text + "▌")


###############
### Globals ###
###############

global lang_dict
global language
global rails_dict
global session
global embedding
global vectorstore
global chat_history
global memory

# RAG options
global disable_vector_store
global strategy
global prompt_type
global custom_prompt
global top_k_vectorstore


#################
### Functions ###
#################

# Function for Vectorizing uploaded data into Astra DB
def vectorize_text(uploaded_files):
    for uploaded_file in uploaded_files:
        if uploaded_file is not None:

            # Write to temporary file
            temp_dir = tempfile.TemporaryDirectory()
            file = uploaded_file
            print(f"""Processing: {file}""")
            temp_filepath = os.path.join(temp_dir.name, file.name)
            with open(temp_filepath, 'wb') as f:
                f.write(file.getvalue())

            # Process TXT
            if uploaded_file.name.endswith('txt'):
                file = [uploaded_file.read().decode()]

                text_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=1500,
                    chunk_overlap=100
                )

                texts = text_splitter.create_documents(file, [{'source': uploaded_file.name}])
                vectorstore.add_documents(texts)
                st.info(f"{len(texts)} {lang_dict['load_text']}")

            # Process PDF
            if uploaded_file.name.endswith('pdf'):
                docs = []
                loader = PyPDFLoader(temp_filepath)
                docs.extend(loader.load())

                text_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=1500,
                    chunk_overlap=100
                )

                pages = text_splitter.split_documents(docs)
                vectorstore.add_documents(pages)
                st.info(f"{len(pages)} {lang_dict['load_pdf']}")

            # Process CSV
            if uploaded_file.name.endswith('csv'):
                docs = []
                loader = CSVLoader(temp_filepath)
                docs.extend(loader.load())

                vectorstore.add_documents(docs)
                st.info(f"{len(docs)} {lang_dict['load_csv']}")


# Load data from URLs
def vectorize_url(urls):
    # Create the text splitter
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1500,
        chunk_overlap=100
    )

    for url in urls:
        try:
            loader = WebBaseLoader(url)
            docs = loader.load()
            pages = text_splitter.split_documents(docs)
            print(f"Loading from URL: {pages}")
            vectorstore.add_documents(pages)
            st.info(f"{len(pages)} loaded")
        except Exception as e:
            st.info(f"An error occurred:", e)


# Define the prompt
def get_prompt(type):
    template = ''

    if type == 'Extended results':
        print("Prompt type: Extended results")
        template = f"""You're a helpful AI assistant tasked to answer the user's questions.
You're friendly and you answer extensively with multiple sentences. You prefer to use bulletpoints to summarize.
If the question states the name of the user, just say 'Thanks, I'll use this information going forward'.
If you don't know the answer, just say 'I do not know the answer'.

Use the following context to answer the question:
{{context}}

Use the following chat history to answer the question:
{{chat_history}}

Question:
{{question}}

Answer in {language}:"""

    if type == 'Short results':
        print("Prompt type: Short results")
        template = f"""You're a helpful AI assistant tasked to answer the user's questions.
You answer in an exceptionally brief way.
If the question states the name of the user, just say 'Thanks, I'll use this information going forward'.
If you don't know the answer, just say 'I do not know the answer'.

Use the following context to answer the question:
{{context}}

Use the following chat history to answer the question:
{{chat_history}}

Question:
{{question}}

Answer in Mongolian:"""

    if type == 'Custom':
        print("Prompt type: Custom")
        template = custom_prompt

    return ChatPromptTemplate.from_messages([("system", template)])


# Get the OpenAI Chat Model
def load_model():
    print(f"""load_model""")
    # Get the OpenAI Chat Model
    return ChatOpenAI(
        temperature=0.3,
        model='gpt-4-1106-preview',
        streaming=True,
        verbose=True
    )


# Get the Retriever
def load_retriever(top_k_vectorstore):
    print(f"""load_retriever with top_k_vectorstore='{top_k_vectorstore}'""")
    # Get the Retriever from the Vectorstore
    return vectorstore.as_retriever(
        search_kwargs={"k": top_k_vectorstore}
    )


@st.cache_resource()
def load_memory(top_k_history):
    print(f"""load_memory with top-k={top_k_history}""")
    return ConversationBufferWindowMemory(
        chat_memory=chat_history,
        return_messages=True,
        k=top_k_history,
        memory_key="chat_history",
        input_key="question",
        output_key='answer',
    )


def generate_queries():
    prompt = f"""You are a helpful assistant that generates multiple search queries based on a single input query in language {language}.
Generate multiple search queries related to: {{original_query}}
OUTPUT (4 queries):"""

    return ChatPromptTemplate.from_messages([("system", prompt)]) | model | StrOutputParser() | (
        lambda x: x.split("\n"))


def reciprocal_rank_fusion(results: list[list], k=60):
    from langchain.load import dumps, loads

    fused_scores = {}
    for docs in results:
        # Assumes the docs are returned in sorted order of relevance
        for rank, doc in enumerate(docs):
            doc_str = dumps(doc)
            if doc_str not in fused_scores:
                fused_scores[doc_str] = 0
            previous_score = fused_scores[doc_str]
            fused_scores[doc_str] += 1 / (rank + k)

    reranked_results = [
        (loads(doc), score)
        for doc, score in sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
    ]
    return reranked_results


# Describe the image based on OpenAI
def describeImage(image_bin, language):
    print("describeImage")
    image_base64 = base64.b64encode(image_bin).decode()
    response = openai.chat.completions.create(
        model="gpt-4-vision-preview",
        messages=[
            {
                "role": "user",
                "content": [
                    #{"type": "text", "text": "Describe the image in detail"},
                    {"type": "text",
                     "text": f"Provide a search text for the main topic of the image writen in {language}"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}",
                        },
                    },
                ],
            }
        ],
        max_tokens=4096,  # default max tokens is low so set higher
    )
    print(f"describeImage result: {response}")
    return response


##################
### Data Cache ###
##################

# Cache localized strings
@st.cache_data()
def load_localization(locale):
    print("load_localization")
    # Load in the text bundle and filter by language locale
    df = pd.read_csv("./customizations/localization.csv")
    df = df.query(f"locale == '{locale}'")
    # Create and return a dictionary of key/values.
    lang_dict = {df.key.to_list()[i]: df.value.to_list()[i] for i in range(len(df.key.to_list()))}
    return lang_dict


# Cache localized strings
@st.cache_data()
def load_rails(username):
    print("load_rails")
    # Load in the rails bundle and filter by username
    df = pd.read_csv("./customizations/rails.csv")
    df = df.query(f"username == '{username}'")
    # Create and return a dictionary of key/values.
    rails_dict = {df.key.to_list()[i]: df.value.to_list()[i] for i in range(len(df.key.to_list()))}
    return rails_dict


#############
### Login ###
#############


username = 'datastax'
language = st.secrets.languages[username]
lang_dict = load_localization(language)


#######################
### Resources Cache ###
#######################

# Cache OpenAI Embedding for future runs
@st.cache_resource(show_spinner=lang_dict['load_embedding'])
def load_embedding():
    print("load_embedding")
    # Get the OpenAI Embedding
    return OpenAIEmbeddings()


# Cache Vector Store for future runs
@st.cache_resource(show_spinner=lang_dict['load_vectorstore'])
def load_vectorstore(username):
    print(f"load_vectorstore for {username}")
    # Get the load_vectorstore store from Astra DB
    return AstraDB(
        embedding=embedding,
        collection_name=f"vector_context_{username}",
        token=st.secrets["ASTRA_TOKEN"],
        api_endpoint=os.environ["ASTRA_ENDPOINT"],
    )


# Cache Chat History for future runs
@st.cache_resource(show_spinner=lang_dict['load_message_history'])
def load_chat_history(username):
    print(f"load_chat_history for {username}_{st.session_state.session_id}")
    return AstraDBChatMessageHistory(
        session_id=f"{username}_{st.session_state.session_id}",
        api_endpoint=os.environ["ASTRA_ENDPOINT"],
        token=st.secrets["ASTRA_TOKEN"],
    )


# Start with empty messages, stored in session state
if 'messages' not in st.session_state:
    st.session_state.messages = [AIMessage(content='Сайн байна уу? танд юугаар туслах вэ?')]

############
### Main ###
############

# Initialize
rails_dict = load_rails(username)
embedding = load_embedding()
vectorstore = load_vectorstore(username)
chat_history = load_chat_history(username)
# Options panel

memory = load_memory(0)

disable_vector_store = False
top_k_vectorstore = 1
strategy = 'Basic Retrieval'
custom_prompt_text = ''
prompt_type = 'Short results'

# Draw all messages, both user and agent so far (every time the app reruns)
for message in st.session_state.messages:
    st.chat_message(message.type).markdown(message.content)

# Now get a prompt from a user
question = st.chat_input('Асуултаа бичнэ үү!!!')

if question:
    print(f"Got question: {question}")
    # Add the prompt to messages, stored in session state
    st.session_state.messages.append(HumanMessage(content=question))
    # Draw the prompt on the page
    print(f"Draw prompt")
    with st.chat_message('human'):
        st.markdown(question)
    # Get model, retriever
    model = load_model()
    retriever = load_retriever(top_k_vectorstore)
    # RAG Strategy
    content = ''
    fusion_queries = []
    relevant_documents = []
    if not disable_vector_store:
        if strategy == 'Basic Retrieval':
            # Basic naive RAG
            relevant_documents = retriever.get_relevant_documents(query=question, k=top_k_vectorstore)
        if strategy == 'Maximal Marginal Relevance':
            relevant_documents = vectorstore.max_marginal_relevance_search(query=question, k=top_k_vectorstore)
        if strategy == 'Fusion':
            # Fusion: Generate new queries and retrieve most relevant documents based on that
            generate_queries = generate_queries()
            fusion_queries = generate_queries.invoke({"original_query": question})
            print(f"""Fusion queries: {fusion_queries}""")

            content += f"""
    
*{lang_dict['using_fusion_queries']}*  
"""
            for fq in fusion_queries:
                content += f"""📙
    """
            # Write the generated fusion queries
            with st.chat_message('assistant'):
                st.markdown(content)

            # Add the answer to the messages session state
            st.session_state.messages.append(AIMessage(content=content))

            chain = generate_queries | retriever.map() | reciprocal_rank_fusion
            relevant_documents = chain.invoke({"original_query": question})
            print(f"""Fusion results: {relevant_documents}""")

    # Get the results from Langchain
    print(f"Chat message")
    with st.chat_message('assistant'):
        content = ''

        # UI placeholder to start filling with agent response
        response_placeholder = st.empty()

        # Get chat history
        history = memory.load_memory_variables({})
        print(f"Using memory: {history}")

        # Create the chain
        inputs = RunnableMap({
            'context': lambda x: x['context'],
            'chat_history': lambda x: x['chat_history'],
            'question': lambda x: x['question']
        })
        print(f"Using inputs: {inputs}")

        chain = inputs | get_prompt(prompt_type) | model
        print(f"Using chain: {chain}")

        # Call the chain and stream the results into the UI
        response = chain.invoke({'question': question, 'chat_history': history, 'context': relevant_documents},
                                config={'callbacks': [StreamHandler(response_placeholder)]})
        print(f"Response: {response}")
        content += response.content

        # Add the result to memory (without the sources)
        memory.save_context({'question': question}, {'answer': content})

        # Write the sources used
        if disable_vector_store:
            content += f"""
"""
        else:
            content += f"""
"""
        sources = []
        for doc in relevant_documents:
            if strategy == 'Fusion':
                doc = doc[0]
            print(f"""DOC: {doc}""")
            source = doc.metadata['source']
            page_content = doc.page_content
            if source not in sources:
                sources.append(source)
        # Write the history used
            content += f"""
"""
        # Write the final answer without the cursor
        response_placeholder.markdown(content)
        # Add the answer to the messages session state
        st.session_state.messages.append(AIMessage(content=content))
