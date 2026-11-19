import type { ReactNode } from 'react';
import type { Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { Link } from '../../i18n/navigation';
import { Badge, Card, InfoIcon, Note } from '../ui';
import type { OpenTasksFilter, OpenTasksPage } from './load-tasks';
import {
  CATEGORIES,
  listingArea,
  listingBudget,
  listingCategory,
  listingWhen,
  type Listing,
} from './listing-text';
import styles from './tasks.module.css';

/** The browse URL for a filter, without the locale (the Link adds it). */
export function browseHref(filter: OpenTasksFilter): string {
  const query = new URLSearchParams();
  if (filter.areaSlug) query.set('area', filter.areaSlug);
  if (filter.category) query.set('category', filter.category);
  if (filter.after) query.set('after', filter.after);
  const search = query.toString();
  return search ? `/tasks?${search}` : '/tasks';
}

/**
 * /{locale}/tasks (BEAV-91, mockup 10 "Browse"): open tasks for anyone, newest published first,
 * the same order for everyone (hard rule k), with category filters and plain links to the next
 * page. Each card shows the approximate area only (hard rule e).
 */
export function BrowseView({
  locale,
  filter,
  page,
}: {
  locale: Locale;
  filter: OpenTasksFilter;
  page: OpenTasksPage | null;
}) {
  const t = useTranslations();
  return (
    <main className={styles.main} data-testid="browse">
      <h1 className={styles.title}>{t('browse.title')}</h1>
      <p className={styles.muted}>{t('browse.intro')}</p>
      <nav aria-label={t('browse.filterLabel')}>
        <ul className={styles.filters}>
          {[undefined, ...CATEGORIES].map((category) => (
            <li key={category ?? 'all'}>
              <Link
                href={browseHref({ areaSlug: filter.areaSlug, category })}
                className={styles.chip}
                aria-current={filter.category === category ? 'true' : undefined}
                data-testid={`browse-category-${category ?? 'all'}`}
              >
                {category ? t(`enums.TaskCategory.${category}`) : t('browse.all')}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
      <p className={styles.muted} data-testid="browse-order">
        {t('browse.order')}
      </p>
      {page === null || page.items.length === 0 ? (
        <p data-testid="browse-empty">{t('browse.empty')}</p>
      ) : (
        <ul className={styles.list} data-testid="browse-list">
          {page.items.map((task) => (
            <li key={task.id}>
              <ListingCard task={task} locale={locale} />
            </li>
          ))}
        </ul>
      )}
      <div className={styles.pager}>
        {filter.after ? (
          <Link
            href={browseHref({ ...filter, after: undefined })}
            className={styles.pagerLink}
            data-testid="browse-first"
          >
            {t('browse.firstPage')}
          </Link>
        ) : null}
        {page?.hasMore && page.endCursor ? (
          <Link
            href={browseHref({ ...filter, after: page.endCursor })}
            className={styles.pagerLink}
            data-testid="browse-more"
          >
            {t('browse.more')}
          </Link>
        ) : null}
      </div>
    </main>
  );
}

function ListingCard({ task, locale }: { task: Listing; locale: Locale }) {
  const t = useTranslations();
  const facts = [listingArea(task, t), listingWhen(task, t, locale)].filter(Boolean).join(' · ');
  return (
    <Link
      href={`/tasks/${encodeURIComponent(task.id)}`}
      className={styles.cardLink}
      data-testid={`browse-task-${task.id}`}
    >
      <Card className={styles.card}>
        <p className={styles.category}>{listingCategory(task, t)}</p>
        <h2 className={styles.cardTitle}>{task.title}</h2>
        <p className={styles.muted}>{facts}</p>
        <p className={styles.budget}>{listingBudget(task, t, locale)}</p>
      </Card>
    </Link>
  );
}

/**
 * /{locale}/tasks/{id} (BEAV-91): a task's public page. It shows what `TaskListing` has, so the
 * approximate area only, never the exact address or access details (hard rule e).
 */
export function TaskPageView({
  task,
  locale,
  actions,
}: {
  task: Listing;
  locale: Locale;
  /** Under the title: the poster's "See offers" island, shown only to the poster. */
  actions?: ReactNode;
}) {
  const t = useTranslations();
  const area = listingArea(task, t);
  return (
    <main className={styles.main} data-testid="task-page">
      <Badge tone="green">{listingCategory(task, t)}</Badge>
      <h1 className={styles.title}>{task.title}</h1>
      {actions}
      <Card className={styles.card}>
        <p data-testid="task-when">{listingWhen(task, t, locale)}</p>
        {area ? <p data-testid="task-area">{area}</p> : null}
        <p className={styles.budget} data-testid="task-budget">
          {listingBudget(task, t, locale)}
        </p>
      </Card>
      <Note icon={<InfoIcon />} data-testid="task-approximate-note">
        {t('taskListing.approximateNote')}
      </Note>
      {task.description ? (
        <section>
          <h2 className={styles.sectionTitle}>{t('taskListing.details')}</h2>
          <p className={styles.description}>{task.description}</p>
        </section>
      ) : null}
      {task.photos.length > 0 ? (
        <section>
          <h2 className={styles.sectionTitle}>{t('taskListing.photos')}</h2>
          <div className={styles.photos}>
            {task.photos.map((photo, index) => (
              // Photos come from the R2 public host; plain img keeps the page free of a
              // remotePatterns entry per environment.
              // eslint-disable-next-line @next/next/no-img-element
              <img
                key={photo.id}
                src={photo.url}
                alt={t('taskListing.photoAlt', { number: index + 1 })}
                className={styles.photo}
                loading="lazy"
              />
            ))}
          </div>
        </section>
      ) : null}
      <Link href="/tasks" className={styles.pagerLink}>
        {t('browse.title')}
      </Link>
    </main>
  );
}
