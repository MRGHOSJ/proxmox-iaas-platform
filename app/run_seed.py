#!/usr/bin/env python3
"""
Seed script
Run this to manually seed the database with initial data.

Usage:
    python run_seed.py

This script creates:
    - Default pod (pod-1) with Proxmox provider
    - Global IP pool (172.16.x.x and 10.x.x.x)
    - VLAN pool (10-4094) for pod-1

This is safe to run multiple times - it uses ON CONFLICT DO NOTHING.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.services.seed import seed_default_pod, seed_global_ip_pool, seed_vlan_pool
from app.models.network import Pod


def main():
    print("=" * 50)
    print("Proxmox Cloud Orchestrator - Database Seed Script")
    print("=" * 50)
    
    db = SessionLocal()
    
    try:
        # Check if pods already exist
        existing_pods = db.query(Pod).count()
        if existing_pods > 0:
            print(f"\nFound {existing_pods} existing pod(s) in the database.")
            response = input("Do you want to continue seeding anyway? (y/N): ")
            if response.lower() != 'y':
                print("Seed cancelled.")
                return
        
        # Seed default pod
        print("\n[1/3] Creating default pod...")
        seed_default_pod(db)
        print("  ✓ Default pod created (pod-1)")
        
        # Seed global IP pool
        print("\n[2/3] Seeding global IP pool (this may take 10-30 seconds)...")
        seed_global_ip_pool(db)
        print("  ✓ Global IP pool seeded (172.16.x.x + 10.x.x.x)")
        
        # Seed VLAN pool for pod-1
        print("\n[3/3] Seeding VLAN pool for pod-1...")
        seed_vlan_pool(db, pod_id=1)
        print("  ✓ VLAN pool seeded (IDs 10-4094)")
        
        print("\n" + "=" * 50)
        print("Seed completed successfully!")
        print("=" * 50)
        print("\nYou can now:")
        print("  1. Start the backend: uvicorn app.main:app --reload")
        print("  2. Access the UI and go to Super Admin -> Pods")
        print("  3. Verify tenant approval works correctly")
        
    except Exception as e:
        print(f"\nError during seeding: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
