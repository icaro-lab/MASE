import React from 'react';
import { cn } from 'lib/utils';

export function TypographyH1({ className, children, ...props }) {
  return (
    <h1 className={cn('scroll-m-20 text-2xl font-semibold tracking-tight', className)} {...props}>
      {children}
    </h1>
  );
}

export function TypographyH2({ className, children, ...props }) {
  return (
    <h2 className={cn('scroll-m-20 text-2xl font-semibold tracking-tight', className)} {...props}>
      {children}
    </h2>
  );
}

export function TypographyLead({ className, children, ...props }) {
  return (
    <p className={cn('text-base text-muted-foreground', className)} {...props}>
      {children}
    </p>
  );
}
