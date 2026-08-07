-- Deterministic fictional governance demo dataset.
-- All personal data is synthetic (@example.com). Metadata quality is the goal.

CREATE ROLE governance_owner NOSUPERUSER NOCREATEDB NOCREATEROLE LOGIN PASSWORD 'governance_owner';

CREATE SCHEMA commerce AUTHORIZATION governance_owner;
COMMENT ON SCHEMA commerce IS 'Commercial transactional schema for governance demo metadata discovery.';

SET ROLE governance_owner;
SET search_path TO commerce;

CREATE TABLE customers (
    customer_id UUID PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    full_name VARCHAR(200) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL,
    notes TEXT
);
COMMENT ON TABLE customers IS 'Registered customers of the fictional commerce platform.';
COMMENT ON COLUMN customers.customer_id IS 'Stable UUID primary key for the customer.';
COMMENT ON COLUMN customers.email IS 'Fictional customer email address.';
COMMENT ON COLUMN customers.full_name IS 'Fictional customer display name.';
COMMENT ON COLUMN customers.is_active IS 'Whether the customer account is active.';
COMMENT ON COLUMN customers.created_at IS 'Account creation timestamp.';
COMMENT ON COLUMN customers.notes IS 'Optional free-text notes.';

CREATE TABLE products (
    product_id BIGSERIAL PRIMARY KEY,
    sku VARCHAR(64) NOT NULL UNIQUE,
    product_name VARCHAR(200) NOT NULL,
    unit_price NUMERIC(12, 2) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    introduced_on DATE NOT NULL
);
COMMENT ON TABLE products IS 'Sellable product catalog.';
COMMENT ON COLUMN products.product_id IS 'Surrogate product identifier.';
COMMENT ON COLUMN products.sku IS 'Stock keeping unit code.';
COMMENT ON COLUMN products.unit_price IS 'List price in demo currency.';
COMMENT ON COLUMN products.is_active IS 'Whether the product can be ordered.';
COMMENT ON COLUMN products.introduced_on IS 'Date the product entered the catalog.';

CREATE TABLE employees (
    employee_id BIGINT PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    full_name VARCHAR(200) NOT NULL,
    title VARCHAR(120) NOT NULL,
    hired_on DATE NOT NULL,
    manager_id BIGINT NULL,
    CONSTRAINT employees_manager_id_fkey
        FOREIGN KEY (manager_id) REFERENCES employees (employee_id)
);
COMMENT ON TABLE employees IS 'Internal employees who may own customer relationships.';
COMMENT ON COLUMN employees.employee_id IS 'Employee primary key.';
COMMENT ON COLUMN employees.manager_id IS 'Optional self-referencing manager relationship.';

CREATE TABLE orders (
    order_id BIGSERIAL PRIMARY KEY,
    customer_id UUID NOT NULL,
    sales_rep_id BIGINT NULL,
    order_status VARCHAR(40) NOT NULL,
    ordered_at TIMESTAMP NOT NULL,
    order_total NUMERIC(12, 2) NOT NULL,
    CONSTRAINT orders_customer_id_fkey
        FOREIGN KEY (customer_id) REFERENCES customers (customer_id),
    CONSTRAINT orders_sales_rep_id_fkey
        FOREIGN KEY (sales_rep_id) REFERENCES employees (employee_id)
);
COMMENT ON TABLE orders IS 'Customer purchase orders.';
COMMENT ON COLUMN orders.order_id IS 'Order primary key.';
COMMENT ON COLUMN orders.customer_id IS 'Customer who placed the order.';
COMMENT ON COLUMN orders.sales_rep_id IS 'Employee credited for the order.';
COMMENT ON COLUMN orders.order_status IS 'Lifecycle status of the order.';
COMMENT ON COLUMN orders.ordered_at IS 'Order placement timestamp.';
COMMENT ON COLUMN orders.order_total IS 'Total monetary value of the order.';

CREATE TABLE order_items (
    order_item_id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL,
    product_id BIGINT NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price NUMERIC(12, 2) NOT NULL,
    CONSTRAINT order_items_order_id_fkey
        FOREIGN KEY (order_id) REFERENCES orders (order_id),
    CONSTRAINT order_items_product_id_fkey
        FOREIGN KEY (product_id) REFERENCES products (product_id),
    CONSTRAINT order_items_quantity_positive CHECK (quantity > 0)
);
COMMENT ON TABLE order_items IS 'Line items belonging to an order.';
COMMENT ON COLUMN order_items.quantity IS 'Ordered quantity for the product.';
COMMENT ON COLUMN order_items.unit_price IS 'Unit price captured at order time.';

CREATE TABLE payments (
    payment_id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    paid_at TIMESTAMP NOT NULL,
    payment_method VARCHAR(40) NOT NULL,
    is_successful BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT payments_order_id_fkey
        FOREIGN KEY (order_id) REFERENCES orders (order_id)
);
COMMENT ON TABLE payments IS 'Payment events associated with orders.';
COMMENT ON COLUMN payments.amount IS 'Paid amount in demo currency.';
COMMENT ON COLUMN payments.payment_method IS 'Payment channel used for settlement.';

CREATE TABLE marketing_contacts (
    contact_id BIGSERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    full_name VARCHAR(200) NOT NULL,
    opted_in BOOLEAN NOT NULL DEFAULT FALSE,
    customer_id UUID NULL,
    created_at TIMESTAMP NOT NULL,
    CONSTRAINT marketing_contacts_customer_id_fkey
        FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
);
COMMENT ON TABLE marketing_contacts IS 'Marketing mailing list contacts, optionally linked to customers.';
COMMENT ON COLUMN marketing_contacts.opted_in IS 'Whether the contact has opted into marketing messages.';
COMMENT ON COLUMN marketing_contacts.customer_id IS 'Optional link to a registered customer.';

-- Deterministic seed data (fictional only).
INSERT INTO customers (customer_id, email, full_name, is_active, created_at, notes) VALUES
    ('11111111-1111-1111-1111-111111111111', 'ada@example.com', 'Ada Example', TRUE, '2024-01-10 09:00:00', 'Founding demo customer'),
    ('22222222-2222-2222-2222-222222222222', 'bob@example.com', 'Bob Example', TRUE, '2024-02-15 10:30:00', NULL),
    ('33333333-3333-3333-3333-333333333333', 'cara@example.com', 'Cara Example', FALSE, '2024-03-20 14:45:00', 'Inactive account');

INSERT INTO products (product_id, sku, product_name, unit_price, is_active, introduced_on) VALUES
    (1, 'SKU-100', 'Demo Widget', 19.99, TRUE, '2023-11-01'),
    (2, 'SKU-200', 'Demo Gadget', 49.50, TRUE, '2024-01-05'),
    (3, 'SKU-300', 'Demo Accessory', 9.25, FALSE, '2022-08-12');

SELECT setval(pg_get_serial_sequence('commerce.products', 'product_id'), 3, true);

INSERT INTO employees (employee_id, email, full_name, title, hired_on, manager_id) VALUES
    (1, 'manager@example.com', 'Morgan Manager', 'Sales Manager', '2020-05-01', NULL),
    (2, 'rep.one@example.com', 'Riley Rep', 'Account Executive', '2021-07-15', 1),
    (3, 'rep.two@example.com', 'Sam Seller', 'Account Executive', '2022-03-10', 1);

INSERT INTO orders (order_id, customer_id, sales_rep_id, order_status, ordered_at, order_total) VALUES
    (1, '11111111-1111-1111-1111-111111111111', 2, 'completed', '2024-04-01 11:00:00', 69.49),
    (2, '22222222-2222-2222-2222-222222222222', 3, 'processing', '2024-04-10 16:20:00', 19.99),
    (3, '11111111-1111-1111-1111-111111111111', 2, 'cancelled', '2024-04-18 08:05:00', 9.25);

SELECT setval(pg_get_serial_sequence('commerce.orders', 'order_id'), 3, true);

INSERT INTO order_items (order_item_id, order_id, product_id, quantity, unit_price) VALUES
    (1, 1, 1, 1, 19.99),
    (2, 1, 2, 1, 49.50),
    (3, 2, 1, 1, 19.99),
    (4, 3, 3, 1, 9.25);

SELECT setval(pg_get_serial_sequence('commerce.order_items', 'order_item_id'), 4, true);

INSERT INTO payments (payment_id, order_id, amount, paid_at, payment_method, is_successful) VALUES
    (1, 1, 69.49, '2024-04-01 11:05:00', 'card', TRUE),
    (2, 2, 19.99, '2024-04-10 16:25:00', 'wallet', TRUE),
    (3, 3, 9.25, '2024-04-18 08:06:00', 'card', FALSE);

SELECT setval(pg_get_serial_sequence('commerce.payments', 'payment_id'), 3, true);

INSERT INTO marketing_contacts (contact_id, email, full_name, opted_in, customer_id, created_at) VALUES
    (1, 'ada@example.com', 'Ada Example', TRUE, '11111111-1111-1111-1111-111111111111', '2024-01-11 09:00:00'),
    (2, 'newsletter@example.com', 'Nora Newsletter', TRUE, NULL, '2024-02-01 12:00:00'),
    (3, 'bob@example.com', 'Bob Example', FALSE, '22222222-2222-2222-2222-222222222222', '2024-02-16 10:30:00');

SELECT setval(pg_get_serial_sequence('commerce.marketing_contacts', 'contact_id'), 3, true);

RESET ROLE;

ALTER SCHEMA commerce OWNER TO governance_owner;
ALTER TABLE commerce.customers OWNER TO governance_owner;
ALTER TABLE commerce.products OWNER TO governance_owner;
ALTER TABLE commerce.employees OWNER TO governance_owner;
ALTER TABLE commerce.orders OWNER TO governance_owner;
ALTER TABLE commerce.order_items OWNER TO governance_owner;
ALTER TABLE commerce.payments OWNER TO governance_owner;
ALTER TABLE commerce.marketing_contacts OWNER TO governance_owner;
