pour vider la base de donner


cd /opt/Projet_N8N
source env/bin/activate
python3 manage.py shell -c "
from asten.models import CommandeAsten
from cyrus.models import CommandeCyrus
from gpv.models import CommandeGPV
from legend.models import CommandeLegend
from imports.models import ImportFichier

CommandeAsten.objects.all().delete()
CommandeCyrus.objects.all().delete()
CommandeGPV.objects.all().delete()
CommandeLegend.objects.all().delete()
ImportFichier.objects.all().delete()

print('Tout supprimé.')
"
