import React from 'react';
import { Card, CardContent } from '../ui/card';

export function EmptyState({ description = 'No data' }) {
  return (
    <Card className="border-dashed">
      <CardContent className="px-4 py-5 text-center text-sm text-muted-foreground">
        {description}
      </CardContent>
    </Card>
  );
}
