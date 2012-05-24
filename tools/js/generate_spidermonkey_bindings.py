#!/usr/bin/python
# ----------------------------------------------------------------------------
# Simple regular expression that obtains super class and protocols from Obj-C
# interfaces
#
# Author: Ricardo Quesada
# Copyright 2012 (C) Zynga, Inc
#
# Dual License: MIT or GPL v2.
# ----------------------------------------------------------------------------
'''
Obtains 
'''

__docformat__ = 'restructuredtext'


# python
import sys
import os
import re
import getopt
import glob
import ast
import xml.etree.ElementTree as ET
import itertools
import copy
import datetime

BINDINGS_PREFIX = 'js_bindings_'
PROXY_PREFIX = 'JSPROXY_'

#
# Templates
#
autogenerated_template = '''/*
* AUTOGENERATED FILE. DO NOT EDIT IT
* Generated by %s on %s
*/
'''

import_template = '''
// needed for callbacks from objective-c to JS
#import <objc/runtime.h>
#import "JRSwizzle.h"

#import "jstypedarray.h"
#import "ScriptingCore.h"   

#import "%s.h"

'''



# xml2d recipe copied from here:
# http://code.activestate.com/recipes/577722-xml-to-python-dictionary-and-back/
def xml2d(e):
    """Convert an etree into a dict structure

    @type  e: etree.Element
    @param e: the root of the tree
    @return: The dictionary representation of the XML tree
    """
    def _xml2d(e):
        kids = dict(e.attrib)
        for k, g in itertools.groupby(e, lambda x: x.tag):
            g = [ _xml2d(x) for x in g ] 
            kids[k]=  g
        return kids
    return { e.tag : _xml2d(e) }


class SpiderMonkey(object):
    def __init__(self, bridgesupport_file, hierarchy_file, classes_to_bind=[] ):
        self.bridgesupport_file = bridgesupport_file
        self.bs = {}

        self.hierarchy_file = hierarchy_file
        self.hierarchy = {}
        
        self.classes_to_bind = set(classes_to_bind)

    def parse_hierarchy_file( self ):
        f = open( self.hierarchy_file )
        self.hierarchy = ast.literal_eval( f.read() )
        f.close()

    def parse_bridgesupport_file( self ):
        p = ET.parse( self.bridgesupport_file )
        root = p.getroot()
        self.bs = xml2d( root )

    def ancestors( self, klass, list_of_ancestors ):
        if klass not in self.hierarchy:
            return list_of_ancestors

        info = self.hierarchy[ klass ]
        subclass =  info['subclass']
        if not subclass:
            return list_of_ancestors

        list_of_ancestors.append( subclass )

        return self.ancestors( subclass, list_of_ancestors )


    def generate_constructor( self, class_name ):

        # Global Variables
        # 1: JSPROXY_CCNode  2: JSPROXY_CCNode
        constructor_globals = '''
JSClass* %s_class = NULL;
JSObject* %s_object = NULL;
'''

        # 1: JSPROXY_CCNode,
        # 2: JSPROXY_CCNode, 3: JSPROXY_CCNode
        # 4: CCNode, 5: CCNode
        # 6: JSPROXY_CCNode,  7: JSPROXY_CCNode
        # 8: possible callback code
        constructor_template = ''' // Constructor
JSBool %s_constructor(JSContext *cx, uint32_t argc, jsval *vp)
{
    JSObject *jsobj = JS_NewObject(cx, %s_class, %s_object, NULL);
    %s *realObj = [%s alloc];

    %s *proxy = [[%s alloc] initWithJSObject:jsobj andRealObject:realObj];

    [realObj release];

    JS_SetPrivate(jsobj, proxy);
    JS_SET_RVAL(cx, vp, OBJECT_TO_JSVAL(jsobj));

    %s
    
    return JS_TRUE;
}
'''
        proxy_class_name = '%s%s' % (PROXY_PREFIX, class_name )
        self.mm_file.write( constructor_globals % ( proxy_class_name, proxy_class_name ) )
        self.mm_file.write( constructor_template % ( proxy_class_name, proxy_class_name, proxy_class_name, class_name, class_name, proxy_class_name, proxy_class_name, '/* no callbacks */' ) )

    def generate_destructor( self, class_name ):
        # 1: JSPROXY_CCNode,
        # 2: JSPROXY_CCNode, 3: JSPROXY_CCNode
        # 4: possible callback code
        destructor_template = '''
// Destructor
void %s_finalize(JSContext *cx, JSObject *obj)
{
	%s *pt = (%s*)JS_GetPrivate(obj);
	if (pt) {
		id real = [pt realObj];
	
	%s

		[real release];
	
		[pt release];

		JS_free(cx, pt);
	}
}
'''
        proxy_class_name = '%s%s' % (PROXY_PREFIX, class_name )
        self.mm_file.write( destructor_template % ( proxy_class_name, proxy_class_name, proxy_class_name, '/* no callbacks */' ) )

    def generate_method( self, class_name, method ):

        # JSPROXY_CCNode, setPosition
        # CCNode
        # CCNode, CCNode
        # 1  (number of arguments)
        method_template = '''
JSBool %s_%s(JSContext *cx, uint32_t argc, jsval *vp) {
	
	JSObject* obj = (JSObject *)JS_THIS_OBJECT(cx, vp);
	JSPROXY_NSObject *proxy = (JSPROXY_NSObject*) JS_GetPrivate( obj );
	NSCAssert( proxy, @"Invalid Proxy object");
	NSCAssert( [proxy isInitialized], @"Object not initialzied. error");
	
	%s * real = (%s*)[proxy realObj];
	NSCAssert( real, @"Invalid real object");

	NSCAssert( argc == %d, @"Invalid number of arguments" );
'''

        # Arguments: int arg0; float arg1; BOOL arg2;...
        # Arguments ids: 'ifB' (int, float, Bool)
        # Arguments addresses: &arg0, &arg1, &arg2
        # ret value + Selector + arguments:  setPosition:arg0 precision:arg1 restorOriginal:arg2]
        arguments_template = '''
    %s
	if (JS_ConvertArguments(cx, argc, JS_ARGV(cx, vp), %s, %s) == JS_TRUE) {
		
		NSCAssert( JS_GetTypedArrayByteLength( arg0 ) == 8, @"Invalid length");
		float *buffer = (float*)JS_GetTypedArrayData(arg0);
		
		%s [real %s];
		
	}
    '''

        return_template = '''
        '''

        end_template = '''
	return JS_TRUE;
}
'''
        supported_types = {
            'f' : 'f',      # float
            'd' : 'f',      # double
            'i' : 'i',      # integer
            'I' : 'i',      # unsigned integer
            'c' : 'c',      # char
            'C' : 'c',      # unsigned char
            'B' : 'b',      # BOOL
            'v' : '',       # void (for retval)
            }

        s = method['selector']
        retval = None
        args = None

        num_of_args = 0
        args_type = []
        ret_type = ''
        supported_args = True
        if 'arg' in method:
            args = method['arg']
            num_of_args = len(args)
            for arg in args:
                t = arg['type']
                if not t in supported_types:
                    supported_args = False
                    break
                else:
                    args_type.append( supported_types[t] )

        if 'retval' in method:
            retval = method['retval']
            t = retval[0]['type']
            if not t in supported_types:
                supported_args = False
            else:
                ret_type = supported_types[t]

        if supported_args:
            print 'OK:' + method['selector'] + ' args:' + str(args_type) + ' ret:' + str(ret_type)
        else:
            print 'NOT OK:' + method['selector']
            return False

        
        # writing...
        s = s.replace(':','_')

        self.mm_file.write( method_template % ( PROXY_PREFIX+class_name, s, class_name, class_name, num_of_args ) )
        if num_of_args > 0:
            self.mm_file.write( arguments_template )
        if ret_type is not '':
            self.mm_file.write( return_template )
        self.mm_file.write( end_template )

        return True

    def generate_methods( self, class_name, klass ):
        for m in klass['method']:
            self.generate_method( class_name, m )

    def generate_header( self, class_name, parent_name ):
        # js_bindindings_CCNode
        # js_bindindings_NSObject
        # JSPROXXY_CCNode
        # JSPROXY_CCNode, JSPROXY_NSObject
        # callback code
        header_template = '''
#import "%s.h"

#import "%s.h"

extern JSObject *%s_object;

/* Proxy class */
@interface %s : %s
{
}
'''
        header_template_end = '''
@end
'''
        proxy_class_name = '%s%s' % (PROXY_PREFIX, class_name )

        # Header file
        self.h_file.write( autogenerated_template % ( sys.argv[0], datetime.date.today() ) )

        self.h_file.write( header_template % (  BINDINGS_PREFIX + class_name, BINDINGS_PREFIX + parent_name, proxy_class_name, proxy_class_name, PROXY_PREFIX + parent_name  ) )
        # callback code should be added here
        self.h_file.write( header_template_end )

    def generate_implementation( self, class_name, parent_name ):
        # 1-12: JSPROXY_CCNode
        implementation_template = '''
+(void) createClassWithContext:(JSContext*)cx object:(JSObject*)globalObj name:(NSString*)name
{
	%s_class = (JSClass *)calloc(1, sizeof(JSClass));
	%s_class->name = [name UTF8String];
	%s_class->addProperty = JS_PropertyStub;
	%s_class->delProperty = JS_PropertyStub;
	%s_class->getProperty = JS_PropertyStub;
	%s_class->setProperty = JS_StrictPropertyStub;
	%s_class->enumerate = JS_EnumerateStub;
	%s_class->resolve = JS_ResolveStub;
	%s_class->convert = JS_ConvertStub;
	%s_class->finalize = %s_finalize;
	%s_class->flags = JSCLASS_HAS_PRIVATE;
'''

        # Properties
        properties_template = '''
	static JSPropertySpec properties[] = {
		{0, 0, 0, 0, 0}
	};
'''
        functions_template = '''
	static JSFunctionSpec funcs[] = {
		JS_FS_END
	};
'''
        static_functions_template = '''
	static JSFunctionSpec st_funcs[] = {
		JS_FS_END
	};
'''
        # 1: JSPROXY_CCNode
        # 2: JSPROXY_NSObject
        # 3-4: JSPROXY_CCNode
        init_class_template = '''
	%s_object = JS_InitClass(cx, globalObj, %s_object, %s_class, %s_constructor,0,properties,funcs,NULL,st_funcs);
}
'''
        proxy_class_name = '%s%s' % (PROXY_PREFIX, class_name )
        proxy_parent_name = '%s%s' % (PROXY_PREFIX, parent_name )

        self.mm_file.write( '\n@implementation %s\n' % proxy_class_name )

        self.mm_file.write( implementation_template % ( proxy_class_name, proxy_class_name, proxy_class_name,
                                                        proxy_class_name, proxy_class_name, proxy_class_name, 
                                                        proxy_class_name, proxy_class_name, proxy_class_name, 
                                                        proxy_class_name, proxy_class_name, proxy_class_name ) )

        self.mm_file.write( properties_template )
        self.mm_file.write( functions_template )
        self.mm_file.write( static_functions_template )
        self.mm_file.write( init_class_template % ( proxy_class_name, proxy_parent_name, proxy_class_name, proxy_class_name ) )

        self.mm_file.write( '\n@end\n' )
    
    def generate_class_binding( self, class_name ):

        self.h_file = open( '%s%s.h' % ( BINDINGS_PREFIX, class_name), 'w' )
        self.mm_file = open( '%s%s.mm' % (BINDINGS_PREFIX, class_name), 'w' )

        signatures = self.bs['signatures']
        classes = signatures['class']
        klass = None

        parent_name = self.hierarchy[ class_name ]['subclass']

        # XXX: Super slow. Add them into a dictionary
        for c in classes:
            if c['name'] == class_name:
                klass = c
                break

        methods = klass['method']

        proxy_class_name = '%s%s' % (PROXY_PREFIX, class_name )


        self.generate_header( class_name, parent_name )

        # Implementation file
        self.mm_file.write( autogenerated_template % ( sys.argv[0], datetime.date.today() ) )
        self.mm_file.write( import_template % (BINDINGS_PREFIX+class_name) )

        self.generate_constructor( class_name )
        self.generate_destructor( class_name )

        self.generate_methods( class_name, klass )

        self.generate_implementation( class_name, parent_name )

        self.h_file.close()
        self.mm_file.close()

    def generate_bindings( self ):
        ancestors = []
        for klass in self.classes_to_bind:
            new_list = self.ancestors( klass, [klass] )      
            ancestors.extend( new_list )

        s = set(ancestors)

        # Explicity remove NSObject. It is generated manually
        copy_set = copy.copy(s)
        for i in copy_set:
            if i.startswith('NS'):
                print 'Removing %s from bindings...' % i
                s.remove( i )

        for klass in s:
            self.generate_class_binding( klass )

    def parse( self ):
        self.parse_hierarchy_file()
        self.parse_bridgesupport_file()

        self.generate_bindings()

def help():
    print "%s v1.0 - An utility to generate SpiderMonkey JS bindings for BridgeSupport files" % sys.argv[0]
    print "Usage:"
    print "\t-b --bridgesupport\tBridgesupport file to parse"
    print "\t-j --hierarchy\tFile that contains the hierarchy class and used protocols"
    print "{class to parse}\tName of the classes to generate. If no classes are "
    print "\nExample:"
    print "\t%s -b cocos2d-mac.bridgesupport -j cocos2d-mac_hierarchy.txt CCNode CCSprite" % sys.argv[0]
    sys.exit(-1)

if __name__ == "__main__":
    if len( sys.argv ) == 1:
        help()

    bridgesupport_file = None
    hierarchy_file = None

    argv = sys.argv[1:]
    try:                                
        opts, args = getopt.getopt(argv, "b:j:", ["bridgesupport=","hierarchy="])

        for opt, arg in opts:
            if opt in ("-b","--bridgesupport"):
                bridgesupport_file = arg
            if opt in  ("-j", "--hierarchy"):
                hierarchy_file = arg
    except getopt.GetoptError,e:
        print e
        opts, args = getopt.getopt(argv, "", [])

    if args == None:
        help()

    instance = SpiderMonkey(bridgesupport_file, hierarchy_file, args )
    instance.parse()
