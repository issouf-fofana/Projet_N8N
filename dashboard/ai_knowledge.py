"""
Base de connaissance RAG pour l'Assistant IA.
Génère des embeddings (NVIDIA NIM) et retrouve par similarité les exemples
question/SQL les plus pertinents pour une nouvelle question, afin de guider
le modèle Text-to-SQL sans avoir à charger le schéma complet à chaque fois.
"""
from django.conf import settings


def get_embedding(text: str, input_type: str = 'query') -> list:
    """
    Génère l'embedding d'un texte via NVIDIA NIM (baai/bge-m3, 1024 dimensions).
    input_type: 'query' pour une question utilisateur, 'passage' pour un document
    (ici une question type stockée) — conservé pour cohérence même si bge-m3 est
    symétrique (pas de distinction stricte query/passage comme nv-embedqa-e5-v5).
    """
    api_key = getattr(settings, 'NVIDIA_API_KEY', '')
    if not api_key:
        raise RuntimeError('Clé API NVIDIA non configurée (requise pour les embeddings).')
    from openai import OpenAI
    client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=api_key, timeout=30.0, max_retries=0)
    response = client.embeddings.create(
        input=[text],
        model='baai/bge-m3',
        extra_body={'input_type': input_type},
    )
    return response.data[0].embedding


def ajouter_connaissance(question: str, sql: str, note: str = '') -> 'AIKnowledgeEntry':
    """Ajoute une entrée à la base de connaissance avec son embedding."""
    from dashboard.models import AIKnowledgeEntry
    embedding = get_embedding(question, input_type='passage')
    return AIKnowledgeEntry.objects.create(question=question, sql=sql, note=note, embedding=embedding)


def rechercher_connaissances_proches(question: str, k: int = 5) -> list:
    """
    Retourne les k entrées de la base de connaissance les plus proches sémantiquement
    de la question posée (distance cosinus via pgvector). Liste vide si la base de
    connaissance est vide ou si la génération d'embedding échoue.
    """
    from dashboard.models import AIKnowledgeEntry
    from pgvector.django import CosineDistance

    if not AIKnowledgeEntry.objects.exists():
        return []

    try:
        query_embedding = get_embedding(question, input_type='query')
    except Exception:
        return []

    return list(
        AIKnowledgeEntry.objects
        .filter(embedding__isnull=False)
        .annotate(distance=CosineDistance('embedding', query_embedding))
        .order_by('distance')[:k]
    )


def construire_contexte_rag(question: str, k: int = 5) -> str:
    """
    Construit un bloc de texte avec les exemples les plus pertinents trouvés,
    à insérer dans le prompt système en complément du schéma de base.
    Retourne une chaîne vide si rien de pertinent n'est trouvé.
    """
    entries = rechercher_connaissances_proches(question, k=k)
    if not entries:
        return ''

    blocs = []
    for e in entries:
        bloc = f"Question similaire : {e.question}\nSQL correct : {e.sql}"
        if e.note:
            bloc += f"\nNote : {e.note}"
        blocs.append(bloc)

    return (
        "\n\n━━━ EXEMPLES PERTINENTS (base de connaissance) ━━━\n"
        "Voici des questions similaires déjà résolues correctement — inspire-toi-en "
        "si la question posée est proche :\n\n" + "\n\n".join(blocs)
    )
