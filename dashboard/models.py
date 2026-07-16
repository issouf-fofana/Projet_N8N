from django.db import models


class AIKnowledgeEntry(models.Model):
    """
    Base de connaissance pour l'Assistant IA (Text-to-SQL).
    Chaque entrée associe une question type (en français) à la requête SQL
    correcte à utiliser, avec une note explicative. L'embedding de la question
    permet de retrouver par similarité les exemples les plus pertinents pour
    guider le modèle IA sur une nouvelle question, au lieu de lui envoyer le
    schéma complet à chaque fois.
    """
    question = models.TextField(verbose_name="Question type (en français)")
    sql = models.TextField(verbose_name="Requête SQL correcte associée")
    note = models.TextField(blank=True, default='', verbose_name="Explication / piège à éviter")
    embedding = models.JSONField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Entrée base de connaissance IA"
        verbose_name_plural = "Entrées base de connaissance IA"
        ordering = ['-date_modification']

    def __str__(self):
        return self.question[:80]
