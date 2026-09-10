import importlib.util,json,sys
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(R/"candidates/_support/src"))
from manim import tempconfig,Rectangle,RoundedRectangle,VMobject
from video_pipeline.token_box import TokenBox
tracked=[]
original=TokenBox.__init__
def token_init(self,*a,**kw):
    original(self,*a,**kw);tracked.append(self)
TokenBox.__init__=token_init
class Stop(Exception):pass
reports=[]
for name,windows,end in [
    ("OLimiteDoDicionarioScene",[(6.7,16.1)],16.2),
    ("ModeloBaseNaoEAssistenteScene",[(7.1,14.2)],15.6),
]:
    if len(sys.argv)>1 and name!=sys.argv[1]:continue
    spec=importlib.util.spec_from_file_location("take",R/"candidates"/name/"scene.py")
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    base=getattr(mod,name)
    report=dict(scene=name,frames_checked=0,token_frames_checked=0,min_token_padding=100,errors=[])
    class Probe(base):
        def at(self,seconds,*a,**kw):
            if name=="ModeloBaseNaoEAssistenteScene" and seconds==15.5:
                visible=[o for r in self.mobjects for o in r.family_members_with_points() if o is not self.camera.frame]
                assert not visible,"JARVIS beat left orphan geometry"
            if seconds>=end:raise Stop()
            return super().at(seconds,*a,**kw)
    with tempconfig(dict(dry_run=True,disable_caching=True,quality="low_quality",frame_rate=30,verbosity="ERROR",progress_bar="none")):
        scene=Probe()
        renderer=scene.renderer
        def inspect(scene,time,moving_mobjects=None):
            t=renderer.time
            renderer.time+=1/30
            if not any(a<=t<=b for a,b in windows):return
            report["frames_checked"]+=1
            live={id(o):o for root in scene.mobjects for o in root.family_members_with_points() if o is not scene.camera.frame}
            if name=="TokensERepresentacoesScene":
                rectangles=[o for o in live.values() if isinstance(o,(Rectangle,RoundedRectangle)) and float(o.get_stroke_opacity())>0.05]
                if t<14.62 or 54.6<t<57.0:
                    if rectangles:report["errors"].append([t,"unexpected rectangle",len(rectangles)])
                for token in tracked:
                    if id(token.rim) not in live:continue
                    if not (18.9<t<28.2 or 33.5<t<53.24):continue
                    r,txt=token.rim,token.text
                    margins=[txt.get_left()[0]-r.get_left()[0],r.get_right()[0]-txt.get_right()[0],txt.get_bottom()[1]-r.get_bottom()[1],r.get_top()[1]-txt.get_top()[1]]
                    report["token_frames_checked"]+=1
                    report["min_token_padding"]=min(report["min_token_padding"],*map(float,margins))
                    if min(margins)<=0:report["errors"].append([t,"token overflow",margins])
            if name=="OLimiteDoDicionarioScene" and t>56.45:
                if any(isinstance(o,(Rectangle,RoundedRectangle)) for o in live.values()):report["errors"].append([t,"contour survives into INFELIZMENTE"])
            frame=scene.camera.frame
            for o in live.values():
                if not isinstance(o,VMobject):continue
                if max(float(np.max(o.get_fill_opacity())),float(np.max(o.get_stroke_opacity())))<0.1:continue
                # The pre-existing hamburger deliberately exits upward/downward
                # at 12.6 s; its offscreen exit is not part of the new HUD.
                if name=="ModeloBaseNaoEAssistenteScene" and t>=12.6 and type(o).__name__=="Arc" and o.get_center()[0]<0:
                    continue
                # Temporary Write/Create partial paths still must fit the frame.
                if o.get_left()[0]<frame.get_left()[0]-0.03 or o.get_right()[0]>frame.get_right()[0]+0.03 or o.get_bottom()[1]<frame.get_bottom()[1]-0.03 or o.get_top()[1]>frame.get_top()[1]+0.03:
                    report["errors"].append([round(t,3),"outside frame",type(o).__name__])
        renderer.render=inspect
        try:scene.render()
        except Stop:pass
    report["errors"]=report["errors"][:30]
    reports.append(report);print(name,report,flush=True)
if len(sys.argv)>1:
    old=json.loads((R/"qa/geometry.json").read_text());by={r["scene"]:r for r in old};by.update({r["scene"]:r for r in reports});reports=list(by.values())
(R/"qa/geometry.json").write_text(json.dumps(reports,indent=2))
assert not any(r["errors"] for r in reports)
