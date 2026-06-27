from django.contrib import admin
from .models import AIKnowledgeEntry


@admin.register(AIKnowledgeEntry)
class AIKnowledgeEntryAdmin(admin.ModelAdmin):
    list_display = ('id', 'question', 'date_modification')
    search_fields = ('question', 'sql', 'note')
    readonly_fields = ('embedding', 'date_creation', 'date_modification')

    def save_model(self, request, obj, form, change):
        from .ai_knowledge import get_embedding
        if 'question' in form.changed_data or not obj.embedding:
            obj.embedding = get_embedding(obj.question, input_type='passage')
        super().save_model(request, obj, form, change)
