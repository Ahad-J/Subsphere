"""Render visual overview diagrams. Full editable Mermaid source lives in docs/diagrams/*.mmd."""
from graphviz import Digraph
from pathlib import Path
OUT = Path(__file__).resolve().parents[2] / 'docs' / 'diagrams'
OUT.mkdir(parents=True,exist_ok=True)

def build_arch():
 g=Digraph('SubSphere_HLD',format='svg')
 g.attr(rankdir='TB',bgcolor='white',pad='0.4',nodesep='0.45',ranksep='0.75',label='SubSphere 4.0  •  Greenfield modular architecture',labelloc='t',fontsize='23',fontname='DejaVu Sans',fontcolor='#183153')
 g.attr('node',shape='box',style='rounded,filled',fontname='DejaVu Sans',fontsize='11',margin='0.19,0.11',color='#c8d5e6',penwidth='1.1')
 g.attr('edge',color='#64748b',arrowsize='.7')
 with g.subgraph(name='cluster_client') as c:
  c.attr(label='USER EXPERIENCE',color='#bad4ff',style='rounded',bgcolor='#f1f6ff',fontname='DejaVu Sans',fontsize='13')
  c.node('i','Individual View\nFree personal subscriptions',fillcolor='#deebff')
  c.node('p','Provider View\nSales · Enterprise · Gym',fillcolor='#deebff')
  c.node('s','SDK / Browser / Desktop\nAuthorized observations',fillcolor='#e7f0ff')
 with g.subgraph(name='cluster_core') as c:
  c.attr(label='OPERATIONAL CORE',color='#9bc0ff',style='rounded',bgcolor='#fbfdff',fontname='DejaVu Sans',fontsize='13')
  c.node('api','FastAPI modular monolith\nAuth · Catalog · Seats · Finance · Usage',fillcolor='#cfe0ff',color='#77a2eb')
  c.node('db','PostgreSQL\nFinancial + quota source of truth',fillcolor='#dae8f7',shape='cylinder')
  c.node('worker','Scheduler / Outbox / Report Workers',fillcolor='#ebf2ff')
 with g.subgraph(name='cluster_integrations') as c:
  c.attr(label='OPTIONAL INTEGRATIONS',color='#ffdb93',style='rounded',bgcolor='#fffcf5',fontname='DejaVu Sans',fontsize='13')
  c.node('ll','LiteLLM\nRouted AI tokens & cost',fillcolor='#fff0cb')
  c.node('om','OpenMeter + ClickHouse\nUsage aggregation',fillcolor='#fff0cb')
  c.node('mail','Postal / Mailpit\nTransactional email',fillcolor='#fff0cb')
  c.node('pdf','WeasyPrint\nInvoice and receipt PDF',fillcolor='#fff0cb')
 with g.subgraph(name='cluster_data') as c:
  c.attr(label='BIG DATA ANALYTICS (OPTIONAL)',color='#c2e5d3',style='rounded',bgcolor='#f4fcf8',fontname='DejaVu Sans',fontsize='13')
  c.node('k','Kafka\nDurable events',fillcolor='#d5f3e3')
  c.node('sp','Spark\nStreaming + batch',fillcolor='#d5f3e3')
  c.node('min','MinIO / Parquet\nBronze · Silver · Gold',fillcolor='#d5f3e3',shape='cylinder')
 g.edges([('i','api'),('p','api'),('s','api'),('api','db'),('api','worker'),('worker','mail'),('worker','pdf'),('worker','k'),('k','sp'),('sp','min'),('api','ll'),('worker','om')])
 g.edge('om','k',style='dashed')
 g.edge('min','db',label='published aggregate',style='dashed',fontsize='9')
 g.render(str(OUT/'subsphere_hld'),cleanup=True)
 g.format='png'; g.render(str(OUT/'subsphere_hld'),cleanup=True)

def build_class():
 g=Digraph('CoreClasses',format='svg')
 g.attr(rankdir='LR',bgcolor='white',pad='.4',nodesep='.32',ranksep='.52',label='SubSphere 4.0  •  Core domain class relationships',labelloc='t',fontsize='21',fontname='DejaVu Sans',fontcolor='#183153')
 g.attr('node',shape='record',fontname='DejaVu Sans',fontsize='10',style='filled',fillcolor='#edf5ff',color='#9ebce6')
 items={
 'Account':'{Account|id: UUID\\lkind: individual / organization\\l}',
 'Organization':'{Organization|id: UUID\\lname: string\\l}',
 'Service':'{Service|id: UUID\\lstatus: enum\\l}',
 'PlanVersion':'{PlanVersion|price_minor: int\\linterval: enum\\l}',
 'Subscription':'{Subscription|id: UUID\\lstatus: enum\\lactivate() / cancel()\\l}',
 'Period':'{SubscriptionPeriod|start: datetime\\lend: datetime\\l}',
 'UsageEvent':'{UsageEvent|event_id: string\\lquantity: int\\l}',
 'UsageCounter':'{UsageCounter|consumed: int\\lconsumeAtomically()\\l}',
 'Invoice':'{Invoice|total_minor: int\\lstatus: enum\\l}',
 'Payment':'{Payment|amount_minor: int\\lconfirmed_at\\l}',
 'Seat':'{SeatAllocation|membership_id\\lrevoked_at\\l}',
 'Personal':'{PersonalSubscription|price_minor\\lnext_renewal\\l}'
 }
 for key,value in items.items():g.node(key,value)
 for a,b,label in [('Account','Organization','0..1'),('Account','Personal','0..n'),('Organization','Service','0..n'),('Service','PlanVersion','0..n'),('PlanVersion','Subscription','0..n'),('Subscription','Period','1..n'),('Period','UsageEvent','0..n'),('Period','UsageCounter','per metric'),('Subscription','Invoice','0..n'),('Invoice','Payment','0..n'),('Subscription','Seat','0..n')]:g.edge(a,b,label=label,fontsize='9',color='#7488a4')
 g.render(str(OUT/'subsphere_class'),cleanup=True)
 g.format='png';g.render(str(OUT/'subsphere_class'),cleanup=True)

def build_usecases():
 g=Digraph('Usecases',format='svg');g.attr(rankdir='LR',pad='.3',bgcolor='white',label='SubSphere 4.0  •  Principal use cases',labelloc='t',fontsize='22',fontname='DejaVu Sans')
 g.attr('node',fontname='DejaVu Sans',fontsize='11',style='filled,rounded',shape='box',fillcolor='#ecf4ff',color='#b7ccef')
 g.node('Individual','Individual\nFree User',shape='ellipse',fillcolor='#d4e5ff')
 g.node('Provider','Business Owner / Admin',shape='ellipse',fillcolor='#d4e5ff')
 g.node('Staff','Staff / Developer',shape='ellipse',fillcolor='#d4e5ff')
 for id,label in [('add','Track own subscriptions'),('usage','Opt-in usage activity'),('cal','Calendar + reminders'),('plan','Create service + plans'),('contact','Add subscribers'),('due','Manage due/overdue fees'),('seats','Allocate enterprise seats'),('invoice','Record payments + receipts'),('metric','Report check-ins / API units'),('kpi','View finances and usage KPIs')]:g.node(id,label)
 for a,b in [('Individual','add'),('Individual','usage'),('Individual','cal'),('Provider','plan'),('Provider','contact'),('Provider','due'),('Provider','seats'),('Provider','invoice'),('Provider','kpi'),('Staff','metric')]:g.edge(a,b,color='#8293a9')
 g.render(str(OUT/'subsphere_usecases'),cleanup=True);g.format='png';g.render(str(OUT/'subsphere_usecases'),cleanup=True)

if __name__=='__main__':
 build_arch();build_class();build_usecases()
 print('Rendered SVG+PNG diagrams in',OUT)
