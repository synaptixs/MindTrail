"""Run real frontend event wiring and check shared progressive navigation structure."""
from pathlib import Path
import subprocess
import unittest
from html.parser import HTMLParser


class GuidedNavigationTests(unittest.TestCase):
    def test_journey_interactions(self):
        result = subprocess.run(['node','--test',str(Path(__file__).parent/'journey.test.cjs')],
                                capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_primary_navigation_has_three_links_and_all_page_ids_stay_unique(self):
        class Structure(HTMLParser):
            def __init__(self):
                super().__init__();self.ids=[];self.nav=False;self.detail=0;self.primary=[]
            def handle_starttag(self, tag, attrs):
                a=dict(attrs)
                if a.get('id'):self.ids.append(a['id'])
                if tag=='nav':self.nav=True
                if self.nav and tag=='details':self.detail+=1
                if self.nav and tag=='a' and not self.detail:self.primary.append(a['href'])
            def handle_endtag(self,tag):
                if self.nav and tag=='details':self.detail-=1
                if tag=='nav':self.nav=False
        for path in (Path(__file__).parent.parent/'src/static').glob('*.html'):
            p=Structure();p.feed(path.read_text(encoding="utf-8"))
            self.assertEqual(len(p.ids),len(set(p.ids)),str(path))
            if p.primary:self.assertEqual(p.primary,['/','/#recorded-periods','/journey.html'],str(path))
