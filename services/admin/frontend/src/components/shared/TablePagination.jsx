import React from 'react';
import { cn } from 'lib/utils';
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationNext,
  PaginationPrevious,
} from '../ui/pagination';

function preventAnchorNavigation(event) {
  event.preventDefault();
}

export function TablePagination({
  page = 1,
  totalPages = 1,
  onPageChange,
  className,
  pageLabel = 'Page',
  'aria-label': ariaLabel,
}) {
  if (totalPages <= 1) return null;

  const canGoPrev = page > 1;
  const canGoNext = page < totalPages;

  return (
    <div className={cn('border-t bg-background px-4 py-3', className)}>
      <Pagination className="justify-end" aria-label={ariaLabel}>
        <PaginationContent>
          <PaginationItem>
            <PaginationPrevious
              href="#"
              aria-disabled={!canGoPrev}
              className={!canGoPrev ? 'pointer-events-none opacity-50' : undefined}
              onClick={(event) => {
                preventAnchorNavigation(event);
                if (!canGoPrev) return;
                onPageChange?.(page - 1);
              }}
            />
          </PaginationItem>
          <PaginationItem>
            <span className="px-2 text-sm text-muted-foreground">
              {pageLabel} {page} / {totalPages}
            </span>
          </PaginationItem>
          <PaginationItem>
            <PaginationNext
              href="#"
              aria-disabled={!canGoNext}
              className={!canGoNext ? 'pointer-events-none opacity-50' : undefined}
              onClick={(event) => {
                preventAnchorNavigation(event);
                if (!canGoNext) return;
                onPageChange?.(page + 1);
              }}
            />
          </PaginationItem>
        </PaginationContent>
      </Pagination>
    </div>
  );
}
