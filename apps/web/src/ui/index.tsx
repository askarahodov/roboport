import React from 'react';
import { Link, NavLink } from 'react-router-dom';
import './ui.css';

type Props = {children: React.ReactNode; className?: string};
export function Container({children,className=''}:Props){return <div className={'ui-container '+className}>{children}</div>;}
export function PageShell({children,className=''}:Props){return <main className={'ui-page '+className}>{children}</main>;}
export function Card({children,className=''}:Props){return <div className={'ui-card '+className}>{children}</div>;}
export function SectionHeading({eyebrow,title,description}:{eyebrow?:string;title:string;description?:string}){return <div className="ui-section-heading">{eyebrow&&<span className="ui-eyebrow">{eyebrow}</span>}<h2>{title}</h2>{description&&<p>{description}</p>}</div>;}
export function Button({children,variant='primary',className='',...props}:React.ButtonHTMLAttributes<HTMLButtonElement>&{variant?:'primary'|'secondary'|'ghost'}){return <button {...props} className={'ui-button ui-button--'+variant+' '+className}>{children}</button>;}
export function LinkButton({children,to,variant='primary'}:{children:React.ReactNode;to:string;variant?:'primary'|'secondary'|'ghost'}){return <Link className={'ui-button ui-button--'+variant} to={to}>{children}</Link>;}
export function Alert({children,tone='error'}:{children:React.ReactNode;tone?:'error'|'warning'|'info'}){return <div role={tone==='info'?'status':'alert'} className={'ui-alert ui-alert--'+tone}>{children}</div>;}
export function EmptyState({title,description,action}:{title:string;description?:string;action?:React.ReactNode}){return <div className="ui-empty"><span aria-hidden="true" className="ui-empty-icon">◇</span><h3>{title}</h3>{description&&<p>{description}</p>}{action}</div>;}
export function Skeleton({label='Загрузка…'}:{label?:string}){return <div className="ui-skeleton" role="status" aria-label={label}><span className="sr-only">{label}</span></div>;}
export function FormField({label,id,children,hint}:{label:string;id:string;children:React.ReactNode;hint?:string}){return <div className="ui-field"><label htmlFor={id}>{label}</label>{children}{hint&&<small>{hint}</small>}</div>;}
export function Badge({children,tone='neutral'}:{children:React.ReactNode;tone?:'neutral'|'success'|'warning'|'error'}){return <span className={'ui-badge ui-badge--'+tone}>{children}</span>;}
export function Tabs({items,label}:{items:{to:string;label:string;end?:boolean}[];label:string}){return <nav className="ui-tabs" aria-label={label}>{items.map(item=><NavLink key={item.to} end={item.end} to={item.to}>{item.label}</NavLink>)}</nav>;}
export function Dialog({open,title,children,onClose}:{open:boolean;title:string;children:React.ReactNode;onClose:()=>void}){
 const ref=React.useRef<HTMLDialogElement>(null);
 React.useEffect(()=>{const node=ref.current;if(!node)return;if(open&&!node.open)node.showModal();if(!open&&node.open)node.close();},[open]);
 return <dialog ref={ref} onClose={onClose} aria-label={title} className="ui-dialog"><div className="ui-dialog-head"><h2>{title}</h2><Button variant="ghost" onClick={onClose} aria-label="Закрыть">✕</Button></div>{children}</dialog>;
}
export function Header({children}:{children:React.ReactNode}){return <header className="ui-header">{children}</header>;}
export function Nav({children,label}:{children:React.ReactNode;label:string}){return <nav className="ui-nav" aria-label={label}>{children}</nav>;}
