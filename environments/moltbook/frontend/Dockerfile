FROM node:20-slim

WORKDIR /app

# Copy package files
COPY package.json .

# Install dependencies
RUN npm install

# Copy application code
COPY . .

# Build the application
RUN npm run build

# Expose port
EXPOSE 3000

ENV BACKEND_URL=http://backend:8000
ENV PORT=3000

# Run the custom server
CMD ["node", "server.js"]
