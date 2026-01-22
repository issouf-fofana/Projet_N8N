from django import forms
from .models import Ticket, SuiviTicket, TicketCategorie, Technicien


class MultipleFileInput(forms.FileInput):
    allow_multiple_selected = True
    
    def __init__(self, attrs=None):
        default_attrs = {'multiple': True}
        if attrs:
            default_attrs.update(attrs)
        super().__init__(default_attrs)
    
    def value_from_datadict(self, data, files, name):
        if hasattr(files, 'getlist'):
            return files.getlist(name)
        value = files.get(name)
        if value:
            return [value]
        return []


class TicketForm(forms.ModelForm):
    nouvelle_categorie = forms.CharField(
        required=False, label="Nouvelle catégorie", widget=forms.TextInput(attrs={"class": "form-control"})
    )
    demandeur = forms.CharField(
        required=False, label="Demandeur", widget=forms.TextInput(attrs={"class": "form-control"})
    )
    fichiers = forms.FileField(
        required=False,
        widget=MultipleFileInput(attrs={"multiple": True, "class": "form-control"}),
        label="Pièces jointes (optionnel)",
    )
    
    def clean_fichiers(self):
        fichiers = self.cleaned_data.get('fichiers')
        if fichiers:
            # Si c'est une liste, retourner la liste
            if isinstance(fichiers, list):
                return fichiers
            # Sinon, retourner une liste avec un seul élément
            return [fichiers]
        return []

    class Meta:
        model = Ticket
        fields = [
            "type_demande",
            "categorie",
            "statut",
            "urgence",
            "impact",
            "magasin",
            "assigne_a",
            "description",
        ]
        widgets = {
            "type_demande": forms.Select(attrs={"class": "form-select"}),
            "categorie": forms.Select(attrs={"class": "form-select"}),
            "statut": forms.Select(attrs={"class": "form-select"}),
            "urgence": forms.Select(attrs={"class": "form-select"}),
            "impact": forms.Select(attrs={"class": "form-select"}),
            "magasin": forms.Select(attrs={"class": "form-select"}),
            "assigne_a": forms.SelectMultiple(attrs={"class": "form-select assign-select", "size": 8}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigne_a"].queryset = Technicien.objects.filter(actif=True)
        self.fields["assigne_a"].required = False
        self.fields["categorie"].required = False

    def clean_fichiers(self):
        # Ne pas valider les fichiers ici, ils seront traités dans la vue
        return None
    
    def clean(self):
        cleaned_data = super().clean()
        nouvelle_categorie = cleaned_data.get("nouvelle_categorie")
        if nouvelle_categorie:
            categorie, _ = TicketCategorie.objects.get_or_create(nom=nouvelle_categorie.strip())
            cleaned_data["categorie"] = categorie
        return cleaned_data


class SuiviTicketForm(forms.ModelForm):
    fichiers = forms.FileField(
        required=False,
        widget=MultipleFileInput(attrs={"multiple": True, "class": "form-control"}),
        label="Pièces jointes",
    )

    class Meta:
        model = SuiviTicket
        fields = ["message"]
        widgets = {
            "message": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["message"].required = False


class StatutTicketForm(forms.Form):
    statut = forms.ChoiceField(choices=Ticket.STATUT_CHOICES, widget=forms.Select(attrs={"class": "form-select"}))


class AssignationTicketForm(forms.Form):
    assigne_a = forms.ModelMultipleChoiceField(
        queryset=Technicien.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select assign-select", "size": 8}),
        label="Assigné à",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigne_a"].queryset = Technicien.objects.filter(actif=True)


class ModifierTicketForm(forms.ModelForm):
    nouvelle_categorie = forms.CharField(
        required=False, 
        label="Nouvelle catégorie (si création)", 
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Laisser vide pour utiliser la catégorie existante"})
    )
    
    class Meta:
        model = Ticket
        fields = [
            "type_demande",
            "categorie",
            "urgence",
            "impact",
            "magasin",
            "demandeur",
            "description",
        ]
        widgets = {
            "type_demande": forms.Select(attrs={"class": "form-select"}),
            "categorie": forms.Select(attrs={"class": "form-select"}),
            "urgence": forms.Select(attrs={"class": "form-select"}),
            "impact": forms.Select(attrs={"class": "form-select"}),
            "magasin": forms.Select(attrs={"class": "form-select"}),
            "demandeur": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }
        labels = {
            "type_demande": "Type",
            "categorie": "Catégorie",
            "urgence": "Urgence",
            "impact": "Impact",
            "magasin": "Magasin",
            "demandeur": "Demandeur",
            "description": "Description",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Magasin
        self.fields["magasin"].queryset = Magasin.objects.all().order_by("code")
        self.fields["categorie"].queryset = TicketCategorie.objects.filter(actif=True).order_by("nom")
        self.fields["categorie"].required = False
        self.fields["demandeur"].required = False
        self.fields["description"].required = False

    def clean(self):
        cleaned_data = super().clean()
        nouvelle_categorie = cleaned_data.get("nouvelle_categorie")
        if nouvelle_categorie and nouvelle_categorie.strip():
            categorie, _ = TicketCategorie.objects.get_or_create(nom=nouvelle_categorie.strip())
            cleaned_data["categorie"] = categorie
        return cleaned_data

