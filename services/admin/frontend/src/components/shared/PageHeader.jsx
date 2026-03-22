import React from 'react';
import { TypographyH1, TypographyLead } from '../ui/typography';

export function PageHeader({ title, description, children }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="space-y-1">
        <TypographyH1>{title}</TypographyH1>
        {description ? (
          <TypographyLead className="text-sm">{description}</TypographyLead>
        ) : null}
      </div>
      {children ? (
        <div className="flex items-center gap-2">{children}</div>
      ) : null}
    </div>
  );
}
