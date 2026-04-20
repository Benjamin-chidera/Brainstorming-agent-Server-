import os
from pinecone import Pinecone, ServerlessSpec
from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from datetime import datetime

# Initialize Pinecone
pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
index_name = "meeting-history"

# Create index if it doesn't exist
if index_name not in pc.list_indexes().names():
    pc.create_index(
        name=index_name,
        dimension=1536, # OpenAI embedding dimension
        metric='cosine',
        spec=ServerlessSpec(cloud='aws', region='us-east-1')
    )

embeddings = OpenAIEmbeddings()
vectorstore = PineconeVectorStore(index_name=index_name, embedding=embeddings)

def sync_message_to_pinecone(meeting_id: str, sender: str, text: str):
    """
    Saves a single message to Pinecone with meeting_id as metadata 
    for isolated semantic retrieval.
    """
    try:
        metadata = {
            "meeting_id": str(meeting_id),
            "sender": sender,
            "timestamp": str(datetime.now())
        }
        vectorstore.add_texts(texts=[text], metadatas=[metadata])
        print(f"[VectorStore] Message synced to Pinecone for meeting {meeting_id}")
    except Exception as e:
        print(f"[VectorStore] Failed to sync to Pinecone: {e}")

def search_meeting_history(meeting_id: str, query: str, k: int = 5):
    """
    Searches for relevant snippets within a specific meeting.
    """
    try:
        results = vectorstore.similarity_search(
            query, 
            k=k, 
            filter={"meeting_id": str(meeting_id)}
        )
        return "\n".join([f"{res.metadata.get('sender')}: {res.page_content}" for res in results])
    except Exception as e:
        print(f"[VectorStore] Search failed: {e}")
        return ""
