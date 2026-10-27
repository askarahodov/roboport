import React from 'react';
import { Alert, EmptyState, Skeleton } from '../../ui';
import { CustomerAutoClaim } from './CustomerAutoClaim';

import { getAutoDevVehicles, AutoDevHttpError } from '../../shared/api/autoDevClient';
import type { AutoDevVehicle } from '../../shared/api/autoDevClient';

type LoadState = 'loading' | 'ready' | 'denied' | 'error';
const PAGE_SIZE = 20;
const MAX_OFFSET = 10000;

export function CustomerVehicles() {
  const [items, setItems] = React.useState<AutoDevVehicle[]>([]);
  const [status, setStatus] = React.useState<LoadState>('loading');
  const [offset, setOffset] = React.useState(0);
  const [nextOffset, setNextOffset] = React.useState<number | null>(null);
  const [pageLoading, setPageLoading] = React.useState(false);
  const [pageError, setPageError] = React.useState(false);
  const [reload, setReload] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    if (offset === 0) {
      setStatus('loading');
      setItems([]);
      setNextOffset(null);
    } else {
      setPageLoading(true);
    }
    setPageError(false);
    void (async () => {
      try {
        // Backend возвращает 200 + пустой список, когда customer link отозвана.
        // Перед расширением списка повторно запрашиваем ВСЕ видимые страницы:
        // иначе карточки с уже отозванным доступом остались бы на экране.
        const fresh: AutoDevVehicle[] = [];
        let following: number | null = null;
        for (let pageOffset = 0; pageOffset <= offset; pageOffset += PAGE_SIZE) {
          let data;
          try {
            data = await getAutoDevVehicles(PAGE_SIZE, pageOffset, controller.signal);
          } catch (error) {
            if (error instanceof AutoDevHttpError
                && [401, 403, 404].includes(error.status)) {
              // Отозванную сессию нельзя оставлять в состоянии кэшированного списка.
              if (!controller.signal.aborted) {
                setItems([]);
                setNextOffset(null);
                setStatus('denied');
              }
              return;
            }
            throw error;
          }
          if (controller.signal.aborted) return;
          // Server-side фильтр по актуальной связи действует для каждой страницы.
          // Offset может пересекать элементы после конкурентных изменений.
          for (const item of data.items) {
            if (!fresh.some(existing => existing.id === item.id)) fresh.push(item);
          }
          following = data.items.length === PAGE_SIZE && pageOffset + PAGE_SIZE <= MAX_OFFSET
            ? pageOffset + PAGE_SIZE : null;
          if (data.items.length < PAGE_SIZE) break;
        }
        if (controller.signal.aborted) return;
        setItems(fresh);
        setNextOffset(following);
        setStatus('ready');
      } catch {
        if (!controller.signal.aborted) {
          if (offset === 0) setStatus('error');
          else setPageError(true);
        }
      } finally {
        if (!controller.signal.aborted) setPageLoading(false);
      }
    })();
    return () => controller.abort();
  }, [offset, reload]);

  function refresh() {
    // Запрос с нуля пересчитывает права и не оставляет устаревший кэш.
    setOffset(0);
    setReload(value => value + 1);
  }

  return <section aria-label="Автомобили в тестовом контуре">
    <p>Тестовый режим Airmatik: отображаются только подтверждённые и неотозванные
      связи. Добавление автомобиля через этот экран пока недоступно.</p>
    {status === 'ready' && <CustomerAutoClaim />}
    {status === 'loading' && <Skeleton label="Загрузка автомобилей…" />}
    {status === 'denied' && <Alert>Доступ к автомобилям не предоставлен или
      закрытый тестовый API выключен. Для проверки обратитесь к администратору стенда.</Alert>}
    {status === 'error' && <p role="alert">Не удалось получить список автомобилей.
      Данные не загружены.</p>}
    {status === 'ready' && (items.length === 0
      ? <EmptyState title="Подтверждённых автомобилей пока нет."
          description="Неподтверждённая заявка не даёт доступ к автомобилю. Проверка проводится сотрудником в тестовом контуре." />
      : <>
        <p role="status" aria-live="polite">Показано автомобилей: {items.length}</p>
        <ul className="account-records">{items.map(vehicle =>
          <li key={vehicle.id} className="account-record">
            <strong>{vehicle.make} {vehicle.model}</strong>
            {vehicle.variant && <span>Комплектация: {vehicle.variant}</span>}
            {vehicle.modelYear !== null && <span>Год выпуска: {vehicle.modelYear}</span>}
            <span>{vehicle.vinLast4 === null ? 'VIN не указан' : 'VIN ••••' + vehicle.vinLast4}</span>
          </li>)}</ul>
        {pageLoading && <p role="status">Загружается следующая страница…</p>}
        {pageError && <p role="alert">Не удалось загрузить следующую страницу.
          Уже полученные автомобили сохранены до обновления списка.</p>}
        {pageError && <button type="button" disabled={pageLoading}
          onClick={() => setReload(value => value + 1)}>Повторить загрузку страницы</button>}
        {nextOffset !== null && !pageError && <button type="button"
          disabled={pageLoading} onClick={() => setOffset(nextOffset)}>
          {pageLoading ? 'Загрузка…' : 'Показать ещё автомобили'}
        </button>}
      </>)}
    {status !== 'loading' && <button type="button"
      onClick={refresh}>Обновить автомобили</button>}
  </section>;
}
