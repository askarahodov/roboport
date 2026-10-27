/** Клиент только для закрытого synthetic API. Не импортировать в production SDK. */
import type { components } from './autoDevSchema';

export type AutoDevVehicle = components['schemas']['AutoVehicle'];
export type AutoDevVehiclePage = components['schemas']['AutoVehiclePage'];

export class AutoDevHttpError extends Error {
  constructor(readonly status: number) {
    super('Auto dev HTTP ' + status);
  }
}

function isAutoVehicle(value: unknown): value is AutoDevVehicle {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const v = value as Record<string, unknown>;
  return Object.keys(v).length === 6
    && typeof v.id === 'string'
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v.id)
    && typeof v.make === 'string' && v.make.trim().length > 0
    && typeof v.model === 'string' && v.model.trim().length > 0
    && (v.variant === null || typeof v.variant === 'string')
    && (v.modelYear === null || (typeof v.modelYear === 'number'
      && Number.isInteger(v.modelYear) && v.modelYear >= 1886 && v.modelYear <= 2100))
    && (v.vinLast4 === null || (typeof v.vinLast4 === 'string'
      && /^[A-HJ-NPR-Z0-9]{4}$/.test(v.vinLast4)));
}

function isAutoPage(value: unknown): value is AutoDevVehiclePage {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const page = value as Record<string, unknown>;
  return Object.keys(page).length === 1 && Array.isArray(page.items)
    && page.items.length <= 50 && page.items.every(isAutoVehicle);
}

/**
 * Доступ к endpoint контролирует сервер — SDK не создаёт полномочий.
 * Запросы всегда same-origin, cookie-only, no-store; данные валидируются даже после HTTP 200.
 */
export async function getAutoDevVehicles(
  limit: number, offset: number, signal?: AbortSignal,
): Promise<AutoDevVehiclePage> {
  if (!Number.isInteger(limit) || limit < 1 || limit > 50
      || !Number.isInteger(offset) || offset < 0 || offset > 10000) {
    throw new RangeError('Invalid dev vehicle pagination');
  }
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  const response = await fetch('/api/v1/dev/auto/vehicles?' + params.toString(), {
    credentials: 'include', cache: 'no-store', signal,
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new AutoDevHttpError(response.status);
  const payload: unknown = await response.json();
  if (!isAutoPage(payload)) throw new Error('Invalid dev vehicle payload');
  return payload;
}
