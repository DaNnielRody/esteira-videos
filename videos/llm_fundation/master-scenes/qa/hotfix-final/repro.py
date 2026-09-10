import importlib.util,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(R/"candidates/_support/src"))
from manim import tempconfig,VMobject,Rectangle,RoundedRectangle
p=Path(sys.argv[1]) if len(sys.argv)>1 else R/"candidates/TokensERepresentacoesScene/scene.py"
spec=importlib.util.spec_from_file_location("take",p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Stop(Exception):pass
class Probe(m.TokensERepresentacoesScene):
    def at(self,seconds,*a,**kw):
        if seconds>=54.9:
            ghosts=[o for root in self.mobjects for o in root.family_members_with_points() if o is not self.camera.frame and isinstance(o,(Rectangle,RoundedRectangle)) and float(o.get_stroke_opacity())>0.05]
            print("GTA residual rectangles:",len(ghosts),[(type(o).__name__,o.get_center().tolist(),o.width) for o in ghosts])
            assert not ghosts,"GTA containers survive into CACHORRO"
            raise Stop()
        return super().at(seconds,*a,**kw)
with tempconfig(dict(dry_run=True,skip_animations=True,quality="low_quality",verbosity="ERROR",progress_bar="none")):
    try:Probe().render()
    except Stop:pass
