-- Contract checks for the governance demo dataset.
\set ON_ERROR_STOP on

DO $$
DECLARE
    table_count INTEGER;
    pk_count INTEGER;
    fk_count INTEGER;
    comment_count INTEGER;
    owner_count INTEGER;
    customers_count INTEGER;
    products_count INTEGER;
    employees_count INTEGER;
    orders_count INTEGER;
    order_items_count INTEGER;
    payments_count INTEGER;
    marketing_contacts_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO table_count
    FROM information_schema.tables
    WHERE table_schema = 'commerce'
      AND table_name IN (
          'customers', 'products', 'employees', 'orders',
          'order_items', 'payments', 'marketing_contacts'
      );
    IF table_count <> 7 THEN
        RAISE EXCEPTION 'expected 7 commerce tables, found %', table_count;
    END IF;

    SELECT COUNT(*) INTO pk_count
    FROM information_schema.table_constraints
    WHERE table_schema = 'commerce'
      AND constraint_type = 'PRIMARY KEY';
    IF pk_count < 7 THEN
        RAISE EXCEPTION 'expected at least 7 primary keys, found %', pk_count;
    END IF;

    SELECT COUNT(*) INTO fk_count
    FROM information_schema.table_constraints
    WHERE table_schema = 'commerce'
      AND constraint_type = 'FOREIGN KEY';
    IF fk_count < 6 THEN
        RAISE EXCEPTION 'expected at least 6 foreign keys, found %', fk_count;
    END IF;

    SELECT COUNT(*) INTO comment_count
    FROM pg_catalog.pg_description d
    JOIN pg_catalog.pg_class c ON c.oid = d.objoid
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'commerce'
      AND c.relkind = 'r'
      AND d.description IS NOT NULL;
    IF comment_count < 7 THEN
        RAISE EXCEPTION 'expected table comments on commerce tables, found %', comment_count;
    END IF;

    SELECT COUNT(*) INTO owner_count
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_catalog.pg_roles r ON r.oid = c.relowner
    WHERE n.nspname = 'commerce'
      AND c.relkind = 'r'
      AND r.rolname = 'governance_owner';
    IF owner_count <> 7 THEN
        RAISE EXCEPTION 'expected governance_owner on 7 tables, found %', owner_count;
    END IF;

    SELECT COUNT(*) INTO customers_count FROM commerce.customers;
    SELECT COUNT(*) INTO products_count FROM commerce.products;
    SELECT COUNT(*) INTO employees_count FROM commerce.employees;
    SELECT COUNT(*) INTO orders_count FROM commerce.orders;
    SELECT COUNT(*) INTO order_items_count FROM commerce.order_items;
    SELECT COUNT(*) INTO payments_count FROM commerce.payments;
    SELECT COUNT(*) INTO marketing_contacts_count FROM commerce.marketing_contacts;

    IF customers_count <> 3
       OR products_count <> 3
       OR employees_count <> 3
       OR orders_count <> 3
       OR order_items_count <> 4
       OR payments_count <> 3
       OR marketing_contacts_count <> 3 THEN
        RAISE EXCEPTION
            'unexpected seed counts customers=% products=% employees=% orders=% order_items=% payments=% marketing_contacts=%',
            customers_count, products_count, employees_count, orders_count,
            order_items_count, payments_count, marketing_contacts_count;
    END IF;

    RAISE NOTICE 'demo dataset contract ok';
END $$;
