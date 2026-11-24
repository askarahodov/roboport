import React from 'react';
import { Link } from 'react-router-dom';
import { Alert, EmptyState, Skeleton } from '../../ui';
import { CustomerAutoClaim } from './CustomerAutoClaim';

import { getAutoDevVehicleCursorPage, getAutoDevVehicleDetails, AutoDevHttpError } from '../../shared/api/autoDevClient';
import type { AutoDevVehicle } from '../../shared/api/autoDevClient';

type LoadState = 'loading' | 'ready' | 'denied' | 'error';
const PAGE_SIZE = 20;
const MAX_PAGES = 501;

export function CustomerVehicles() {
  const [items, setItems] = React.useState<AutoDevVehicle[]>([]);
  const [status, setStatus] = React.useState<LoadState>('loading');
  const [pageIndex, setPageIndex] = React.useState(0);
  const [nextCursor, setNextCursor] = React.useState<string | null>(null);
  const [pageLoading, setPageLoading] = React.useState(false);
  const [pageError, setPageError] = React.useState(false);
  const [reload, setReload] = React.useState(0);
  const [selected, setSelected] = React.useState<AutoDevVehicle | null>(null);
  const [detailStatus, setDetailStatus] = React.useState<'idle' | 'loading' | 'error'>('idle');
  const detailAbort = React.useRef<AbortController | null>(null);
  const listAbort = React.useRef<AbortController | null>(null);
  React.useEffect(() => () => {
    detailAbort.current?.abort();
    listAbort.current?.abort();
  }, []);

  function clearDetails() {
    detailAbort.current?.abort();
    detailAbort.current = null;
    setSelected(null);
    setDetailStatus('idle');
  }

  async function showDetails(vehicleId: string) {
    clearDetails();
    const controller = new AbortController();
    detailAbort.current = controller;
    setDetailStatus('loading');
    try {
      const detail = await getAutoDevVehicleDetails(vehicleId, controller.signal);
      if (!controller.signal.aborted) {
        setSelected(detail);
        setDetailStatus('idle');
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      setSelected(null);
      setDetailStatus('error');
      if (error instanceof AutoDevHttpError) {
        if (error.status === 401 || error.status === 403) {
          // Отказ доступа должен инвалидировать и незавершённую загрузку
          // списка: запоздавший HTTP 200 не вправе восстановить старые данные.
          listAbort.current?.abort();
          setPageLoading(false);
          setItems([]);
          setNextCursor(null);
          setStatus('denied');
        } else if (error.status === 404) {
          // Подтверждённая связь может быть отозвана в любой момент:
          // сразу скрыть карточку и заново проверить все страницы на сервере.
          setItems(previous => previous.filter(vehicle => vehicle.id !== vehicleId));
          refresh();
        }
      }
    }
  }

  React.useEffect(() => {
    const controller = new AbortController();
    listAbort.current?.abort();
    listAbort.current = controller;
    if (pageIndex === 0) {
      clearDetails();
      setStatus('loading');
      setItems([]);
      setNextCursor(null);
    } else {
      setPageLoading(true);
    }
    setPageError(false);
    void (async () => {
      const fresh:AutoDevVehicle[]=[];
      let following:string|null=null;
      try {
        // Backend возвращает 200 + пустой список, когда customer link отозвана.
        // Перед расширением списка повторно запрашиваем ВСЕ видимые страницы:
        // иначе карточки с уже отозванным доступом остались бы на экране.
        const seenCursors=new Set<string>();
        for(let index=0;index<=pageIndex;index++){
          let data;
          try {
            data = await getAutoDevVehicleCursorPage(PAGE_SIZE, following, controller.signal);
          } catch (error) {
            if (error instanceof AutoDevHttpError
                && [401, 403, 404].includes(error.status)) {
              // Отозванную сессию нельзя оставлять в состоянии кэшированного списка.
              if (!controller.signal.aborted) {
                clearDetails();
                setItems([]);
                setNextCursor(null);
                setStatus('denied');
              }
              return;
            }
            throw error;
          }
          if (controller.signal.aborted) return;
          // Server-side фильтр по актуальной связи действует для каждой страницы.
          // Cursor does not shift when newer rows are inserted or revoked.
          for (const item of data.items) {
            if (!fresh.some(existing => existing.id === item.id)) fresh.push(item);
          }
          following=data.nextCursor;
          if(following===null)break;
          if(seenCursors.has(following))throw Error('Repeated vehicle cursor');
          seenCursors.add(following);
        }
        if (controller.signal.aborted) return;
        if (selected && !fresh.some(vehicle => vehicle.id === selected.id)) clearDetails();
        setItems(fresh);
        setNextCursor(pageIndex+1<MAX_PAGES?following:null);
        setStatus('ready');
      } catch {
        if (!controller.signal.aborted) {
          // Keep only pages freshly re-authorized during this attempt.
          clearDetails();
          setItems(fresh);
          setNextCursor(null);
          if(pageIndex===0||fresh.length===0)setStatus('error');
          else setPageError(true);
        }
      } finally {
        if (!controller.signal.aborted) setPageLoading(false);
      }
    })();
    return () => controller.abort();
  }, [pageIndex, reload]);

  function refresh() {
    clearDetails();
    // Запрос с нуля пересчитывает права и не оставляет устаревший кэш.
    setPageIndex(0);
    setReload(value => value + 1);
  }

  return <section aria-label="Автомобили в тестовом контуре">
    <section aria-labelledby="vehicle-help-title" className="account-help">
      <h3 id="vehicle-help-title">Помощь по автомобилям (dev)</h3>
      <p>Раздел работает только с искусственными данными. Не вводите реальные VIN,
        документы или персональные сведения.</p>
      <details>
        <summary>Почему я не вижу автомобиль?</summary>
        <p>В списке появляются только машины с подтверждённой и действующей связью.
          Пока заявка ожидает решения, обратитесь к тестовому приёмщику.
          Самостоятельное добавление автомобиля пока недоступно.</p>
      </details>
      <details>
        <summary>Что делать, если доступ запрещён или отозван?</summary>
        <p>Проверьте тестовую учётную запись. При отзыве прав повторный вход их
          не восстановит — обратитесь к администратору стенда. Не подменяйте UUID,
          не пытайтесь открыть чужую историю.</p>
      </details>
      <details>
        <summary>Почему список не загружается?</summary>
        <p>Проверьте доступность тестового стенда, затем нажмите «Обновить автомобили».
          Для следующей страницы используйте «Повторить загрузку страницы».
          Не отправляйте одноразовые коды в поддержку.</p>
      </details>
      <details>
        <summary>Где запись, история ремонта, сметы и документы?</summary>
        <p>Список заказ-нарядов и техническое согласование последней версии сметы
          доступны в закрытом синтетическом dev-контуре через <Link to="/account/auto-orders">Мои заказ-наряды (dev)</Link>.
          Запись, перенос и отмена собственной записи доступны в закрытом synthetic dev;
          реальные платежи, юридические документы и выдача автомобиля пока не реализованы.</p>
      </details>
      <p><a href="/docs/#/guides/client">Полная клиентская инструкция БоксРитм</a></p>
    </section>
    <p>Тестовый режим БоксРитм: отображаются только подтверждённые и неотозванные
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
            <button type="button" disabled={detailStatus === 'loading'}
              onClick={() => void showDetails(vehicle.id)}
              aria-label={'Подробнее об автомобиле ' + vehicle.make + ' ' + vehicle.model}>
              Подробнее об автомобиле
            </button>
          </li>)}</ul>
        {detailStatus === 'loading' && <p role="status">Проверка доступа к автомобилю…</p>}
        {detailStatus === 'error' && <p role="alert">Карточка автомобиля недоступна:
          доступ мог быть отозван либо сеть временно недоступна. Обновите автомобили.</p>}
        {selected && <section aria-label="Карточка подтверждённого автомобиля"
          className="account-record" data-testid="vehicle-details">
          <h3>Карточка автомобиля</h3>
          <p><strong>{selected.make} {selected.model}</strong></p>
          {selected.variant && <p>Комплектация: {selected.variant}</p>}
          {selected.modelYear !== null && <p>Год выпуска: {selected.modelYear}</p>}
          <p>{selected.vinLast4 === null ? 'VIN не указан' : 'VIN ••••' + selected.vinLast4}</p>
          <p>Только подтверждённая действующая связь. История прежних владельцев недоступна.</p>
          <button type="button" onClick={clearDetails}>Закрыть карточку автомобиля</button>
        </section>}
        {pageLoading && <p role="status">Загружается следующая страница…</p>}
        {pageError && <p role="alert">Не удалось загрузить следующую страницу.
          Показаны только повторно проверенные автомобили.</p>}
        {pageError && <button type="button" disabled={pageLoading}
          onClick={() => setReload(value => value + 1)}>Повторить загрузку страницы</button>}
        {nextCursor !== null && !pageError && <button type="button"
          disabled={pageLoading} onClick={() => setPageIndex(index=>index+1)}>
          {pageLoading ? 'Загрузка…' : 'Показать ещё автомобили'}
        </button>}
      </>)}
    {status !== 'loading' && <button type="button"
      onClick={refresh}>Обновить автомобили</button>}
  </section>;
}
