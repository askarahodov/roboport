import type { paths } from './schema';

/** Public read-only SDK boundary, generated response types from canonical OpenAPI. */
type Brands = paths['/api/v1/catalog/brands']['get']['responses']['200']['content']['application/json'];
type Models = paths['/api/v1/catalog/models']['get']['responses']['200']['content']['application/json'];
export type CatalogPath='/api/v1/catalog/brands'|'/api/v1/catalog/models';
export type CatalogResult<P extends CatalogPath>=P extends '/api/v1/catalog/brands'?Brands:Models;

export class CatalogHttpError extends Error {
  constructor(readonly status:number) {super('Catalog HTTP '+status);}
}

export async function getCatalog<P extends CatalogPath>(
  path:P, params:URLSearchParams=new URLSearchParams(), signal?:AbortSignal
):Promise<CatalogResult<P>>{
  const url=path+(params.size?'?'+params.toString():'');
  const response=await fetch(url,{credentials:'omit',cache:'no-store',signal,headers:{Accept:'application/json'}});
  if(!response.ok)throw new CatalogHttpError(response.status);
  const payload:unknown=await response.json();
  if(!payload||typeof payload!=='object'||!('items' in payload)||
    !Array.isArray(payload.items)||!payload.items.every((item:unknown)=>
      item!==null&&typeof item==='object'&&'id' in item&&typeof item.id==='string'&&
      'name' in item&&typeof item.name==='string'))throw new Error('Malformed catalog response');
  return payload as CatalogResult<P>;
}
